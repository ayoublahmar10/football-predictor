import json
from pathlib import Path

from app.models.schemas import ComboHistoryEntry, ComboRecommendation

_HISTORY_FILE = Path("data/combo_history.json")


def _load() -> dict:
    if not _HISTORY_FILE.exists():
        return {"entries": [], "next_id": 1}
    return json.loads(_HISTORY_FILE.read_text(encoding="utf-8"))


def _persist(data: dict) -> None:
    _HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    _HISTORY_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def add_combo(combo: ComboRecommendation, leagues: str, min_odds: float) -> ComboHistoryEntry:
    data = _load()
    entry = ComboHistoryEntry(
        id=data["next_id"],
        leagues=leagues,
        min_odds=min_odds,
        combo=combo,
    )
    data["entries"].append(entry.model_dump(mode="json"))
    data["next_id"] += 1
    _persist(data)
    return entry


def get_history() -> list[ComboHistoryEntry]:
    """Retourne les entrées les plus récentes en premier."""
    data = _load()
    return [ComboHistoryEntry.model_validate(e) for e in reversed(data["entries"])]


def delete_entry(entry_id: int) -> bool:
    data = _load()
    before = len(data["entries"])
    data["entries"] = [e for e in data["entries"] if e["id"] != entry_id]
    if len(data["entries"]) < before:
        _persist(data)
        return True
    return False
