import asyncio
import logging
import traceback
from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger(__name__)

from app.config import settings
from datetime import datetime, timezone
from math import prod
from app.models.schemas import Prediction, LeagueResponse, MatchAnalysisInput, ComboPick, ComboRecommendation, ComboHistoryEntry
from app.services.football_api import FootballAPIClient
from app.services.predictor import FootballPredictor
from app.services import history as combo_history

router = APIRouter()
football_client = FootballAPIClient()
predictor = FootballPredictor()


@router.get("/debug/fixtures/{competition_code}")
async def debug_fixtures(competition_code: str):
    """Endpoint de debug : retourne la réponse brute de football-data.org."""
    try:
        data = await football_client._get(
            f"competitions/{competition_code}/matches",
            {"season": settings.current_season},
        )
        all_matches = data.get("matches", [])
        upcoming = [m for m in all_matches if m.get("status") in ("SCHEDULED", "TIMED")]
        return {
            "competition_code": competition_code,
            "season": settings.current_season,
            "total_matches": len(all_matches),
            "upcoming_count": len(upcoming),
            "errors": data.get("message"),
            "preview": upcoming[:1],
        }
    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}


@router.get("/debug/predict-one/{competition_code}")
async def debug_predict_one(competition_code: str):
    """Debug : tente une prédiction sur le 1er match disponible et retourne les erreurs détaillées."""
    steps = {}
    try:
        # Étape 1 : récupérer les fixtures
        fixtures = await football_client.get_fixtures(competition_code, next_n=1)
        steps["fixtures"] = f"OK — {len(fixtures)} match(s) trouvé(s)"
        if not fixtures:
            return {"steps": steps, "error": "Aucun match à venir trouvé"}

        fixture = fixtures[0]
        steps["fixture"] = f"{fixture.home_team.name} vs {fixture.away_team.name} ({fixture.date})"

        # Étape 2 : stats équipe domicile
        home_stats = await football_client.get_team_statistics(fixture.home_team.id, competition_code, is_home=True)
        steps["home_stats"] = f"OK — {home_stats.team_name} ({home_stats.wins}V/{home_stats.draws}N/{home_stats.losses}D)"

        # Étape 3 : stats équipe extérieure
        away_stats = await football_client.get_team_statistics(fixture.away_team.id, competition_code, is_home=False)
        steps["away_stats"] = f"OK — {away_stats.team_name} ({away_stats.wins}V/{away_stats.draws}N/{away_stats.losses}D)"

        # Étape 4 : H2H
        h2h = await football_client.get_h2h(fixture.id)
        steps["h2h"] = f"OK — {len(h2h)} confrontation(s)"

        # Étape 5 : prédiction IA
        match_input = MatchAnalysisInput(fixture=fixture, home_stats=home_stats, away_stats=away_stats, h2h=h2h)
        prediction = await predictor.analyze_match(match_input)
        steps["prediction"] = "OK"

        return {"steps": steps, "prediction": prediction}

    except Exception as e:
        steps["error"] = str(e)
        steps["traceback"] = traceback.format_exc()
        return {"steps": steps}


@router.get("/leagues", response_model=list[LeagueResponse])
async def get_leagues():
    """Retourne les 5 grands championnats supportés."""
    league_countries = {
        "PL": "England",
        "PD": "Spain",
        "BL1": "Germany",
        "SA": "Italy",
        "FL1": "France",
    }
    return [
        LeagueResponse(id=code, name=name, country=league_countries[code])
        for code, name in settings.SUPPORTED_LEAGUES.items()
    ]


@router.get("/predictions", response_model=list[Prediction])
async def get_predictions(
    leagues: str = Query(
        default="PL,PD,BL1,SA,FL1",
        description="Codes des championnats séparés par des virgules (ex: PL,PD,BL1)",
    ),
    next: int = Query(default=5, ge=1, le=20, description="Nombre de matchs par championnat"),
):
    """
    Retourne les prédictions IA pour les prochains matchs des championnats sélectionnés.
    Codes disponibles : PL (Premier League), PD (La Liga), BL1 (Bundesliga), SA (Serie A), FL1 (Ligue 1)
    """
    league_codes = [c.strip().upper() for c in leagues.split(",")]

    unsupported = [c for c in league_codes if c not in settings.SUPPORTED_LEAGUES]
    if unsupported:
        raise HTTPException(
            status_code=400,
            detail=f"Codes non supportés : {unsupported}. Codes valides : {list(settings.SUPPORTED_LEAGUES.keys())}",
        )

    return await _fetch_predictions(league_codes, next)


CONFIDENCE_ORDER = {"high": 0, "medium": 1, "low": 2}


async def _fetch_predictions(
    league_codes: list[str],
    next_n: int,
    fast_mode: bool = False,
    prediction_limit: int | None = None,
) -> list[Prediction]:
    """Logique commune de récupération des prédictions, partagée entre /predictions et /combo.

    fast_mode=True : skip les recent_matches (économise 2 appels API par fixture).
    prediction_limit : arrêt anticipé une fois ce nombre de prédictions atteint.
    """
    fixture_tasks = [football_client.get_fixtures(code, next_n=next_n) for code in league_codes]
    all_fixtures_per_league = await asyncio.gather(*fixture_tasks, return_exceptions=True)

    all_fixtures = []
    for result in all_fixtures_per_league:
        if isinstance(result, Exception):
            continue
        all_fixtures.extend(result)

    predictions = []
    groq_quota_exhausted = False

    for fixture in all_fixtures:
        if prediction_limit is not None and len(predictions) >= prediction_limit:
            break

        try:
            home_stats, away_stats = await asyncio.gather(
                football_client.get_team_statistics(fixture.home_team.id, fixture.league.id, is_home=True, include_recent=not fast_mode),
                football_client.get_team_statistics(fixture.away_team.id, fixture.league.id, is_home=False, include_recent=not fast_mode),
            )
        except Exception as e:
            logger.warning("Skipping fixture %d (stats error): %s", fixture.id, e)
            continue

        try:
            h2h = await football_client.get_h2h(fixture.id)
        except Exception as e:
            logger.warning("Fixture %d: h2h unavailable (%s), predicting without it", fixture.id, e)
            h2h = []

        try:
            match_input = MatchAnalysisInput(fixture=fixture, home_stats=home_stats, away_stats=away_stats, h2h=h2h)
            prediction = await predictor.analyze_match(match_input)
            predictions.append(prediction)
        except Exception as e:
            err_str = str(e)
            if "rate_limit_exceeded" in err_str and "TPD" in err_str:
                groq_quota_exhausted = True
            logger.warning("Skipping fixture %d (prediction error): %s", fixture.id, e)
            continue

    if not predictions and groq_quota_exhausted:
        raise HTTPException(
            status_code=503,
            detail=(
                "Quota Groq journalier épuisé (limite : 100k tokens/jour). "
                "Réessayez demain ou consultez l'historique de vos combinés."
            ),
        )

    return predictions


def _best_pick_confidence(p: Prediction) -> str:
    """Retourne la confiance associée au best_pick."""
    if "BTTS" in p.best_pick:
        return p.btts.confidence
    if p.best_pick_odds == p.goals.estimated_odds:
        return p.goals.confidence
    if p.best_pick_odds == p.result_1x2.estimated_odds:
        return p.result_1x2.confidence
    return p.btts.confidence


def _pick_sort_key(p: Prediction) -> tuple:
    return (CONFIDENCE_ORDER[_best_pick_confidence(p)], -p.best_pick_odds)


@router.get("/combo", response_model=ComboRecommendation)
async def get_combo(
    leagues: str = Query(default="PL,PD,BL1,SA,FL1", description="Codes des championnats"),
    next: int = Query(default=5, ge=1, le=15, description="Matchs analysés par championnat"),
    max_picks: int = Query(default=10, ge=2, le=10, description="Nombre maximum de sélections"),
    min_odds: float = Query(default=100.0, ge=2.0, description="Cote totale minimale cible"),
):
    """
    Génère un combiné recommandé : sélectionne les picks les plus confiants jusqu'à atteindre
    la cote cible (min_odds) ou la limite de sélections (max_picks).
    """
    league_codes = [c.strip().upper() for c in leagues.split(",")]
    unsupported = [c for c in league_codes if c not in settings.SUPPORTED_LEAGUES]
    if unsupported:
        raise HTTPException(status_code=400, detail=f"Codes non supportés : {unsupported}")

    predictions = await _fetch_predictions(league_codes, next, fast_mode=False, prediction_limit=max_picks)
    if len(predictions) < 2:
        raise HTTPException(status_code=404, detail="Pas assez de matchs disponibles pour générer un combiné (minimum 2)")

    # Tri : confiance (high > medium > low), puis cote décroissante à confiance égale
    sorted_preds = sorted(predictions, key=_pick_sort_key)

    # Sélection gloutonne : on ajoute des picks jusqu'à atteindre min_odds ou max_picks
    selected = []
    running_odds = 1.0
    for pred in sorted_preds:
        if len(selected) >= max_picks:
            break
        selected.append(pred)
        running_odds *= pred.best_pick_odds
        if running_odds >= min_odds:
            break

    combo_picks = [
        ComboPick(
            fixture_id=p.fixture.id,
            match=f"{p.fixture.home_team.name} vs {p.fixture.away_team.name}",
            league=p.fixture.league.name,
            date=p.fixture.date.strftime("%d/%m/%Y %H:%M"),
            pick=p.best_pick,
            odds=p.best_pick_odds,
            confidence=_best_pick_confidence(p),
        )
        for p in selected
    ]

    total_odds = round(prod(cp.odds for cp in combo_picks), 2)

    result = ComboRecommendation(
        picks=combo_picks,
        total_odds=total_odds,
        return_per_100=round(total_odds * 100, 2),
        generated_at=datetime.now(timezone.utc),
        target_reached=total_odds >= min_odds,
    )
    combo_history.add_combo(result, leagues, min_odds)
    return result


@router.get("/history", response_model=list[ComboHistoryEntry])
async def get_combo_history():
    """Retourne l'historique des combinés générés, du plus récent au plus ancien."""
    return combo_history.get_history()


@router.delete("/history/{entry_id}", status_code=204)
async def delete_combo_history_entry(entry_id: int):
    """Supprime une entrée de l'historique des combinés."""
    if not combo_history.delete_entry(entry_id):
        raise HTTPException(status_code=404, detail=f"Entrée {entry_id} introuvable")


@router.get("/predictions/{fixture_id}", response_model=Prediction)
async def get_prediction_by_fixture(fixture_id: int):
    """
    Retourne la prédiction IA pour un match spécifique (par son fixture_id football-data.org).
    """
    fixture = await football_client.get_fixture_by_id(fixture_id)
    if fixture is None:
        raise HTTPException(status_code=404, detail=f"Match {fixture_id} introuvable")

    if fixture.league.id not in settings.SUPPORTED_LEAGUES:
        raise HTTPException(
            status_code=400,
            detail=f"Ce match appartient à un championnat non supporté (code={fixture.league.id})",
        )

    try:
        home_stats, away_stats, h2h = await asyncio.gather(
            football_client.get_team_statistics(
                fixture.home_team.id, fixture.league.id, is_home=True
            ),
            football_client.get_team_statistics(
                fixture.away_team.id, fixture.league.id, is_home=False
            ),
            football_client.get_h2h(fixture.id),
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Erreur récupération stats : {str(e)}")

    match_input = MatchAnalysisInput(
        fixture=fixture,
        home_stats=home_stats,
        away_stats=away_stats,
        h2h=h2h,
    )

    try:
        prediction = await predictor.analyze_match(match_input)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Erreur analyse IA : {str(e)}")

    return prediction
