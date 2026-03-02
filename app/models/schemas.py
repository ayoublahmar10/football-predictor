from datetime import datetime
from typing import Literal
from pydantic import BaseModel


class TeamInfo(BaseModel):
    id: int
    name: str
    logo: str


class LeagueInfo(BaseModel):
    id: str  # code competition ex: "PL", "PD", "BL1"
    name: str
    country: str
    logo: str
    flag: str | None = None


class Fixture(BaseModel):
    id: int
    date: datetime
    home_team: TeamInfo
    away_team: TeamInfo
    league: LeagueInfo
    venue: str | None = None


class RecentMatch(BaseModel):
    date: str
    home_team: str
    away_team: str
    home_goals: int
    away_goals: int
    result: Literal["W", "D", "L"]


class TeamStats(BaseModel):
    team_id: int
    team_name: str
    form: str
    goals_scored_avg: float
    goals_conceded_avg: float
    home_goals_scored_avg: float | None = None
    home_goals_conceded_avg: float | None = None
    away_goals_scored_avg: float | None = None
    away_goals_conceded_avg: float | None = None
    wins: int
    draws: int
    losses: int
    recent_matches: list[RecentMatch] = []


class H2HMatch(BaseModel):
    date: str
    home_team: str
    away_team: str
    home_goals: int
    away_goals: int


class MatchAnalysisInput(BaseModel):
    fixture: Fixture
    home_stats: TeamStats
    away_stats: TeamStats
    h2h: list[H2HMatch]


class ResultPrediction(BaseModel):
    outcome: Literal["1", "X", "2"]
    confidence: Literal["high", "medium", "low"]
    reasoning: str
    estimated_odds: float


class GoalsPrediction(BaseModel):
    prediction: Literal["Over 2.5", "Under 2.5"]
    confidence: Literal["high", "medium", "low"]
    reasoning: str
    estimated_odds: float


class BttsPrediction(BaseModel):
    prediction: Literal["Yes", "No"]
    confidence: Literal["high", "medium", "low"]
    reasoning: str
    estimated_odds: float


class Prediction(BaseModel):
    fixture: Fixture
    result_1x2: ResultPrediction
    goals: GoalsPrediction
    btts: BttsPrediction
    best_pick: str        # ex: "Over 2.5" ou "Arsenal (1)" ou "BTTS Yes"
    best_pick_odds: float
    summary: str
    generated_at: datetime


class LeagueResponse(BaseModel):
    id: str
    name: str
    country: str


class ComboPick(BaseModel):
    fixture_id: int
    match: str
    league: str
    date: str
    pick: str
    odds: float
    confidence: Literal["high", "medium", "low"]


class ComboRecommendation(BaseModel):
    picks: list[ComboPick]
    total_odds: float
    return_per_100: float
    generated_at: datetime
    target_reached: bool = False


class ComboHistoryEntry(BaseModel):
    id: int
    leagues: str       # ex: "PL,PD,BL1,SA,FL1"
    min_odds: float
    combo: ComboRecommendation
