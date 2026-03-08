"""Last.fm authentication and scrobbling."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
from typing import Callable

import requests

CONFIG_PATH = Path.home() / ".local" / "share" / "hifi-tui" / "lastfm.json"
API_URL = "https://ws.audioscrobbler.com/2.0/"


def _md5(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()


class LastFM:
    def __init__(self) -> None:
        self._cfg: dict = {}
        self._load()

    # ── persistence ──────────────────────────────────────────────────────────

    def _load(self) -> None:
        try:
            self._cfg = json.loads(CONFIG_PATH.read_text())
        except Exception:
            self._cfg = {}

    def _save(self) -> None:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(self._cfg, indent=2))

    # ── properties ───────────────────────────────────────────────────────────

    @property
    def is_configured(self) -> bool:
        return bool(self._cfg.get("api_key") and self._cfg.get("api_secret"))

    @property
    def is_authenticated(self) -> bool:
        return bool(self._cfg.get("session_key"))

    @property
    def username(self) -> str:
        return self._cfg.get("username", "")

    @property
    def api_key(self) -> str:
        return self._cfg.get("api_key", "")

    @property
    def api_secret(self) -> str:
        return self._cfg.get("api_secret", "")

    # ── API helpers ───────────────────────────────────────────────────────────

    def _sig(self, params: dict) -> str:
        keys = sorted(k for k in params if k not in ("format", "callback"))
        s = "".join(f"{k}{params[k]}" for k in keys)
        s += self._cfg.get("api_secret", "")
        return _md5(s)

    def _call(self, params: dict, post: bool = False) -> dict:
        params = dict(params)
        params["format"] = "json"
        if post:
            resp = requests.post(API_URL, data=params, timeout=10)
        else:
            resp = requests.get(API_URL, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            raise RuntimeError(f"Last.fm error {data['error']}: {data.get('message', '')}")
        return data

    # ── credentials ──────────────────────────────────────────────────────────

    def set_credentials(self, api_key: str, api_secret: str) -> None:
        self._cfg["api_key"] = api_key.strip()
        self._cfg["api_secret"] = api_secret.strip()
        self._cfg.pop("session_key", None)
        self._cfg.pop("username", None)
        self._save()

    # ── auth flow ─────────────────────────────────────────────────────────────

    def get_auth_token(self) -> str:
        params = {
            "method": "auth.getToken",
            "api_key": self._cfg["api_key"],
        }
        params["api_sig"] = self._sig(params)
        return self._call(params)["token"]

    def get_auth_url(self, token: str) -> str:
        return f"https://www.last.fm/api/auth/?api_key={self._cfg['api_key']}&token={token}"

    def complete_auth(self, token: str) -> str:
        """Exchange approved token for session key. Returns username."""
        params = {
            "method": "auth.getSession",
            "api_key": self._cfg["api_key"],
            "token": token,
        }
        params["api_sig"] = self._sig(params)
        session = self._call(params)["session"]
        self._cfg["session_key"] = session["key"]
        self._cfg["username"] = session["name"]
        self._save()
        return session["name"]

    def disconnect(self) -> None:
        self._cfg.pop("session_key", None)
        self._cfg.pop("username", None)
        self._save()

    # ── scrobbling ────────────────────────────────────────────────────────────

    def update_now_playing(self, artist: str, track: str, album: str, duration: int) -> None:
        params = {
            "method": "track.updateNowPlaying",
            "api_key": self._cfg["api_key"],
            "sk": self._cfg["session_key"],
            "artist": artist,
            "track": track,
            "album": album,
            "duration": str(duration),
        }
        params["api_sig"] = self._sig(params)
        self._call(params, post=True)

    def scrobble(self, artist: str, track: str, album: str, duration: int, timestamp: int) -> None:
        params = {
            "method": "track.scrobble",
            "api_key": self._cfg["api_key"],
            "sk": self._cfg["session_key"],
            "artist[0]": artist,
            "track[0]": track,
            "album[0]": album,
            "duration[0]": str(duration),
            "timestamp[0]": str(timestamp),
        }
        params["api_sig"] = self._sig(params)
        self._call(params, post=True)


class Scrobbler:
    """Tracks listening state and fires Last.fm scrobbles at the right time."""

    def __init__(self, lastfm: LastFM) -> None:
        self._lfm = lastfm
        self._track = None
        self._start_ts: int = 0
        self._now_playing_sent = False
        self._scrobbled = False
        self._lock = threading.Lock()

    def track_started(self, track) -> None:
        with self._lock:
            self._track = track
            self._start_ts = int(time.time())
            self._now_playing_sent = False
            self._scrobbled = False

    def reset(self) -> None:
        with self._lock:
            self._track = None

    def update(self, position: float, on_error: Callable[[str], None] | None = None) -> None:
        if not self._lfm.is_authenticated:
            return
        with self._lock:
            track = self._track
            if track is None:
                return
            duration = track.duration or 1
            threshold = min(duration / 2, 4 * 60)
            needs_now_playing = not self._now_playing_sent and position >= 5
            needs_scrobble = not self._scrobbled and position >= threshold
            if needs_now_playing:
                self._now_playing_sent = True
            if needs_scrobble:
                self._scrobbled = True
            start_ts = self._start_ts

        if not needs_now_playing and not needs_scrobble:
            return

        def _run():
            if needs_now_playing:
                try:
                    self._lfm.update_now_playing(track.artist, track.title, track.album, duration)
                except Exception as e:
                    if on_error:
                        on_error(f"now playing: {e}")
            if needs_scrobble:
                try:
                    self._lfm.scrobble(track.artist, track.title, track.album, duration, start_ts)
                except Exception as e:
                    if on_error:
                        on_error(f"scrobble: {e}")

        threading.Thread(target=_run, daemon=True).start()
