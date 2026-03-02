import asyncio
import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

from groq import AsyncGroq

from app.config import settings
from app.models.schemas import (
    MatchAnalysisInput,
    Prediction,
    ResultPrediction,
    GoalsPrediction,
    BttsPrediction,
)

_CACHE_FILE = Path("data/predictions_cache.json")
_CACHE_TTL = timedelta(hours=12)


def _parse_groq_retry_after(error: Exception) -> float | None:
    """Parse 'Please try again in Xm Y.Zs' ou 'in Y.Zs' depuis les erreurs 429 de Groq."""
    m = re.search(r'in (?:(\d+)m)?(\d+(?:\.\d+)?)s', str(error))
    if m:
        return float(m.group(1) or 0) * 60 + float(m.group(2))
    return None


def _load_disk_cache() -> dict:
    if not _CACHE_FILE.exists():
        return {}
    try:
        return json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _build_prompt(data: MatchAnalysisInput) -> str:
    fix = data.fixture
    home = data.home_stats
    away = data.away_stats

    h2h_text = ""
    if data.h2h:
        h2h_lines = [
            f"  {m.date}: {m.home_team} {m.home_goals}-{m.away_goals} {m.away_team}"
            for m in data.h2h
        ]
        h2h_text = "Historique des confrontations (5 derniers):\n" + "\n".join(h2h_lines)
    else:
        h2h_text = "Pas d'historique de confrontations disponible."

    home_recent = "\n".join(
        f"  {m.date}: {m.home_team} {m.home_goals}-{m.away_goals} {m.away_team} [{m.result}]"
        for m in home.recent_matches
    ) or "  Aucune donnée"

    away_recent = "\n".join(
        f"  {m.date}: {m.home_team} {m.home_goals}-{m.away_goals} {m.away_team} [{m.result}]"
        for m in away.recent_matches
    ) or "  Aucune donnée"

    return f"""Analyse ce match de football et fournis tes prédictions avec cotes.

MATCH : {fix.home_team.name} vs {fix.away_team.name}
Compétition : {fix.league.name} ({fix.league.country})
Date : {fix.date.strftime('%d/%m/%Y %H:%M')}
Lieu : {fix.venue or 'Non renseigné'}

--- ÉQUIPE DOMICILE : {home.team_name} ---
Forme récente (5 matchs) : {home.form}
Bilan saison : {home.wins}V / {home.draws}N / {home.losses}D
Buts marqués/match (moy) : {home.goals_scored_avg}
Buts encaissés/match (moy) : {home.goals_conceded_avg}
À domicile — marqués : {home.home_goals_scored_avg}, encaissés : {home.home_goals_conceded_avg}
5 derniers matchs :
{home_recent}

--- ÉQUIPE EXTÉRIEURE : {away.team_name} ---
Forme récente (5 matchs) : {away.form}
Bilan saison : {away.wins}V / {away.draws}N / {away.losses}D
Buts marqués/match (moy) : {away.goals_scored_avg}
Buts encaissés/match (moy) : {away.goals_conceded_avg}
À l'extérieur — marqués : {away.away_goals_scored_avg}, encaissés : {away.away_goals_conceded_avg}
5 derniers matchs :
{away_recent}

--- {h2h_text} ---"""


SYSTEM_PROMPT = """Tu es un expert en analyse de football et paris sportifs. Tu analyses des statistiques de matchs et fournis des prédictions avec des cotes estimées réalistes (style bookmaker européen).

Règles pour les cotes estimées :
- Cotes en format décimal européen (ex: 1.50, 2.30, 3.75)
- Fourchette réaliste : entre 1.20 et 5.00
- Les cotes doivent refléter les probabilités (équipe favorite = cote basse)
- BTTS Yes typiquement entre 1.55 et 2.20, BTTS No entre 1.60 et 2.10
- Over 2.5 typiquement entre 1.55 et 2.30, Under 2.5 entre 1.60 et 2.40

Le "best_pick" est le pari unique le plus recommandé parmi les 3 options (résultat, buts, BTTS). Choisis celui avec le meilleur rapport confiance/valeur.

Tu réponds UNIQUEMENT en JSON valide avec exactement cette structure :
{
  "result_1x2": {
    "outcome": "1" ou "X" ou "2",
    "confidence": "high" ou "medium" ou "low",
    "reasoning": "justification courte",
    "estimated_odds": 1.75
  },
  "goals": {
    "prediction": "Over 2.5" ou "Under 2.5",
    "confidence": "high" ou "medium" ou "low",
    "reasoning": "justification courte",
    "estimated_odds": 1.85
  },
  "btts": {
    "prediction": "Yes" ou "No",
    "confidence": "high" ou "medium" ou "low",
    "reasoning": "justification courte",
    "estimated_odds": 1.70
  },
  "best_pick": "le meilleur pari ex: Over 2.5 ou Arsenal (1) ou BTTS Yes",
  "best_pick_odds": 1.85,
  "summary": "analyse narrative courte du match (2-3 phrases)"
}
Aucun texte en dehors du JSON."""


class FootballPredictor:
    def __init__(self):
        self._client = AsyncGroq(api_key=settings.groq_api_key)
        self._cache_lock = asyncio.Lock()
        # Cache disque : survit aux redémarrages, évite de rebrûler des tokens pour les mêmes fixtures
        self._disk_cache: dict[str, dict] = _load_disk_cache()
        self._mem_cache: dict[int, Prediction] = {}
        # Peupler le cache mémoire depuis le disque (entrées encore valides)
        now = datetime.now(timezone.utc)
        for fid_str, entry in self._disk_cache.items():
            try:
                cached_at = datetime.fromisoformat(entry["cached_at"])
                if now - cached_at < _CACHE_TTL:
                    self._mem_cache[int(fid_str)] = Prediction.model_validate(entry["prediction"])
            except Exception:
                pass

    async def analyze_match(self, data: MatchAnalysisInput) -> Prediction:
        if data.fixture.id in self._mem_cache:
            return self._mem_cache[data.fixture.id]

        prompt = _build_prompt(data)
        last_error: Exception | None = None

        for attempt in range(2):
            try:
                response = await self._client.chat.completions.create(
                    model="llama-3.3-70b-versatile",
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.3,
                )
                raw = response.choices[0].message.content.strip()
                pred_data = json.loads(raw)

                prediction = Prediction(
                    fixture=data.fixture,
                    result_1x2=ResultPrediction(**pred_data["result_1x2"]),
                    goals=GoalsPrediction(**pred_data["goals"]),
                    btts=BttsPrediction(**pred_data["btts"]),
                    best_pick=pred_data["best_pick"],
                    best_pick_odds=float(pred_data["best_pick_odds"]),
                    summary=pred_data["summary"],
                    generated_at=datetime.now(timezone.utc),
                )
                await self._store(data.fixture.id, prediction)
                return prediction

            except Exception as e:
                last_error = e
                if attempt == 0:
                    wait = _parse_groq_retry_after(e)
                    # Retry seulement si le délai est raisonnable (≤ 5 min)
                    if wait is not None and wait <= 300:
                        await asyncio.sleep(wait + 1)
                        continue
                break

        raise last_error  # type: ignore[misc]

    async def _store(self, fixture_id: int, prediction: Prediction) -> None:
        async with self._cache_lock:
            self._mem_cache[fixture_id] = prediction
            self._disk_cache[str(fixture_id)] = {
                "cached_at": datetime.now(timezone.utc).isoformat(),
                "prediction": prediction.model_dump(mode="json"),
            }
            _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            _CACHE_FILE.write_text(
                json.dumps(self._disk_cache, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
