import asyncio
import time
from datetime import date, datetime, timezone
import httpx
from app.config import settings
from app.models.schemas import (
    Fixture,
    TeamInfo,
    LeagueInfo,
    TeamStats,
    RecentMatch,
    H2HMatch,
)

LEAGUE_COUNTRIES = {
    "PL": "England",
    "PD": "Spain",
    "BL1": "Germany",
    "SA": "Italy",
    "FL1": "France",
}


class FootballAPIClient:
    def __init__(self):
        self._headers = {"X-Auth-Token": settings.football_data_key}
        self._base_url = settings.FOOTBALL_DATA_BASE_URL
        self._standings_cache: dict[str, dict] = {}
        self._recent_matches_cache: dict[int, list[RecentMatch]] = {}
        # Serialize requests with a minimum interval to stay under 10 req/min free plan
        self._request_lock = asyncio.Lock()
        self._last_request_at: float = 0.0
        self._min_interval: float = 7.0  # ~8.5 req/min, safely under the 10/min limit

    async def _get(self, endpoint: str, params: dict | None = None) -> dict:
        async with self._request_lock:
            gap = time.monotonic() - self._last_request_at
            if gap < self._min_interval:
                await asyncio.sleep(self._min_interval - gap)
            self._last_request_at = time.monotonic()

            async with httpx.AsyncClient(timeout=30.0) as client:
                for attempt in range(4):
                    response = await client.get(
                        f"{self._base_url}/{endpoint}",
                        headers=self._headers,
                        params=params or {},
                    )
                    if response.status_code == 429:
                        # Exponential backoff: 15s, 30s, 60s, 120s
                        await asyncio.sleep(15 * (2 ** attempt))
                        continue
                    response.raise_for_status()
                    return response.json()
                response.raise_for_status()
                return response.json()

    def _parse_fixture(self, item: dict, competition_code: str) -> Fixture:
        comp = item.get("competition", {})
        home = item["homeTeam"]
        away = item["awayTeam"]
        return Fixture(
            id=item["id"],
            date=item["utcDate"],
            home_team=TeamInfo(
                id=home["id"],
                name=home.get("name") or home.get("shortName", ""),
                logo=home.get("crest", ""),
            ),
            away_team=TeamInfo(
                id=away["id"],
                name=away.get("name") or away.get("shortName", ""),
                logo=away.get("crest", ""),
            ),
            league=LeagueInfo(
                id=competition_code,
                name=comp.get("name", settings.SUPPORTED_LEAGUES.get(competition_code, "")),
                country=LEAGUE_COUNTRIES.get(competition_code, ""),
                logo=comp.get("emblem", ""),
            ),
            venue=item.get("venue"),
        )

    async def get_fixtures(self, competition_code: str, next_n: int = 5) -> list[Fixture]:
        data = await self._get(
            f"competitions/{competition_code}/matches",
            {"season": settings.current_season},
        )
        now = datetime.now(timezone.utc)
        upcoming = [
            m for m in data.get("matches", [])
            if m.get("status") in ("SCHEDULED", "TIMED")
            and datetime.fromisoformat(m["utcDate"].replace("Z", "+00:00")) >= now
        ][:next_n]
        return [self._parse_fixture(m, competition_code) for m in upcoming]

    async def get_fixture_by_id(self, fixture_id: int) -> Fixture | None:
        data = await self._get(f"matches/{fixture_id}")
        if not data:
            return None
        comp_code = data.get("competition", {}).get("code", "")
        return self._parse_fixture(data, comp_code)

    async def _get_standings(self, competition_code: str) -> dict:
        """Retourne les standings TOTAL, HOME, AWAY sous forme de dict {team_id: row}."""
        if competition_code in self._standings_cache:
            return self._standings_cache[competition_code]

        data = await self._get(f"competitions/{competition_code}/standings")
        result: dict[str, dict[int, dict]] = {"TOTAL": {}, "HOME": {}, "AWAY": {}}

        for standing in data.get("standings", []):
            stype = standing.get("type", "TOTAL")
            if stype not in result:
                continue
            for row in standing.get("table", []):
                team_id = row["team"]["id"]
                result[stype][team_id] = row

        self._standings_cache[competition_code] = result
        return result

    async def get_team_statistics(
        self, team_id: int, competition_code: str, is_home: bool, include_recent: bool = True
    ) -> TeamStats:
        standings = await self._get_standings(competition_code)
        total = standings["TOTAL"].get(team_id, {})
        home_s = standings["HOME"].get(team_id, {})
        away_s = standings["AWAY"].get(team_id, {})

        played = total.get("playedGames", 1) or 1
        played_home = home_s.get("playedGames", 1) or 1
        played_away = away_s.get("playedGames", 1) or 1

        # form ex: "W,W,D,L,W" → "WWDLW"
        raw_form = total.get("form") or ""
        form = raw_form.replace(",", "")[-5:]

        recent = await self._get_recent_matches(team_id) if include_recent else []

        return TeamStats(
            team_id=team_id,
            team_name=total.get("team", {}).get("name", ""),
            form=form,
            goals_scored_avg=round(total.get("goalsFor", 0) / played, 2),
            goals_conceded_avg=round(total.get("goalsAgainst", 0) / played, 2),
            home_goals_scored_avg=round(home_s.get("goalsFor", 0) / played_home, 2),
            home_goals_conceded_avg=round(home_s.get("goalsAgainst", 0) / played_home, 2),
            away_goals_scored_avg=round(away_s.get("goalsFor", 0) / played_away, 2),
            away_goals_conceded_avg=round(away_s.get("goalsAgainst", 0) / played_away, 2),
            wins=total.get("won", 0),
            draws=total.get("draw", 0),
            losses=total.get("lost", 0),
            recent_matches=recent,
        )

    async def _get_recent_matches(self, team_id: int) -> list[RecentMatch]:
        if team_id in self._recent_matches_cache:
            return self._recent_matches_cache[team_id]

        data = await self._get(
            f"teams/{team_id}/matches",
            {"status": "FINISHED", "limit": 5},
        )
        matches = []
        for item in data.get("matches", []):
            score = item.get("score", {}).get("fullTime", {})
            home_goals = score.get("home") or 0
            away_goals = score.get("away") or 0
            is_home_team = item["homeTeam"]["id"] == team_id

            if is_home_team:
                result = "W" if home_goals > away_goals else ("D" if home_goals == away_goals else "L")
            else:
                result = "W" if away_goals > home_goals else ("D" if away_goals == home_goals else "L")

            matches.append(
                RecentMatch(
                    date=item["utcDate"][:10],
                    home_team=item["homeTeam"].get("name", ""),
                    away_team=item["awayTeam"].get("name", ""),
                    home_goals=home_goals,
                    away_goals=away_goals,
                    result=result,
                )
            )
        self._recent_matches_cache[team_id] = matches
        return matches

    async def get_h2h(self, fixture_id: int, limit: int = 5) -> list[H2HMatch]:
        data = await self._get(f"matches/{fixture_id}/head2head", {"limit": limit})
        matches = []
        for item in data.get("matches", []):
            score = item.get("score", {}).get("fullTime", {})
            matches.append(
                H2HMatch(
                    date=item["utcDate"][:10],
                    home_team=item["homeTeam"].get("name", ""),
                    away_team=item["awayTeam"].get("name", ""),
                    home_goals=score.get("home") or 0,
                    away_goals=score.get("away") or 0,
                )
            )
        return matches
