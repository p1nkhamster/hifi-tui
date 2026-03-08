"""Playlist management — one JSON file per playlist."""

from __future__ import annotations

import json
import re
from pathlib import Path

PLAYLISTS_DIR = Path.home() / ".local" / "share" / "hifi-tui" / "playlists"


def _ensure_dir() -> Path:
    PLAYLISTS_DIR.mkdir(parents=True, exist_ok=True)
    return PLAYLISTS_DIR


def _slug(name: str) -> str:
    return re.sub(r"[^\w\-]", "_", name).strip("_") or "playlist"


def _path(name: str) -> Path:
    return _ensure_dir() / f"{_slug(name)}.json"


def list_playlists() -> list[dict]:
    """Return [{name, track_count}] for all playlists, sorted by name."""
    result = []
    for f in sorted(_ensure_dir().glob("*.json")):
        try:
            data = json.loads(f.read_text())
            result.append({
                "name": data.get("name", f.stem),
                "track_count": len(data.get("tracks", [])),
            })
        except Exception:
            pass
    return result


def load_playlist(name: str) -> list[dict]:
    """Return list of track storage dicts for the named playlist."""
    p = _path(name)
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text()).get("tracks", [])
    except Exception:
        return []


def save_playlist(name: str, tracks: list[dict]) -> None:
    _path(name).write_text(json.dumps({"name": name, "tracks": tracks}, indent=2))


def create_playlist(name: str) -> None:
    """Create an empty playlist if one doesn't already exist."""
    if not _path(name).exists():
        save_playlist(name, [])


def add_tracks(name: str, new_tracks: list[dict]) -> int:
    """Append tracks to a playlist, skipping duplicates. Returns count added."""
    existing = load_playlist(name)
    existing_ids = {t["track_id"] for t in existing}
    added = 0
    for t in new_tracks:
        if t["track_id"] not in existing_ids:
            existing.append(t)
            existing_ids.add(t["track_id"])
            added += 1
    save_playlist(name, existing)
    return added


def remove_track(name: str, index: int) -> None:
    tracks = load_playlist(name)
    if 0 <= index < len(tracks):
        tracks.pop(index)
        save_playlist(name, tracks)


def move_track(name: str, from_index: int, to_index: int) -> None:
    tracks = load_playlist(name)
    if 0 <= from_index < len(tracks) and 0 <= to_index < len(tracks):
        track = tracks.pop(from_index)
        tracks.insert(to_index, track)
        save_playlist(name, tracks)


def rename_playlist(old_name: str, new_name: str) -> None:
    """Rename a playlist: update the name field and move to new slug filename."""
    old_path = _path(old_name)
    if not old_path.exists():
        return
    tracks = load_playlist(old_name)
    new_path = _path(new_name)
    new_path.write_text(json.dumps({"name": new_name, "tracks": tracks}, indent=2))
    if old_path != new_path:
        old_path.unlink()


def delete_playlist(name: str) -> None:
    p = _path(name)
    if p.exists():
        p.unlink()
