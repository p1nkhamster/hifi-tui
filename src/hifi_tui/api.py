"""HiFi API client."""

from __future__ import annotations

import base64
import http.server
import socketserver
import tempfile
import threading
import os
import xml.etree.ElementTree as ET
from typing import Any

import requests

# Holds a reference to the currently-active MPD HTTP server so it stays alive
# while a DASH track is playing. Replaced on each new DASH track.
_active_mpd_server: socketserver.TCPServer | None = None
_active_mpd_tmpfile: str | None = None

BASE_URL = "https://hifi.p1nkhamster.xyz"
SESSION = requests.Session()
SESSION.timeout = 15


def _get(path: str, **params: Any) -> Any:
    resp = SESSION.get(f"{BASE_URL}{path}", params={k: v for k, v in params.items() if v is not None})
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def search_tracks(query: str, limit: int = 25, offset: int = 0) -> list[dict]:
    data = _get("/search/", s=query, limit=limit, offset=offset)
    return data["data"]["items"]


def search_albums(query: str, limit: int = 25, offset: int = 0) -> list[dict]:
    data = _get("/search/", al=query, limit=limit, offset=offset)
    return data["data"]["albums"]["items"]


def search_artists(query: str, limit: int = 25, offset: int = 0) -> list[dict]:
    data = _get("/search/", a=query, limit=limit, offset=offset)
    return data["data"]["artists"]["items"]


# ---------------------------------------------------------------------------
# Track
# ---------------------------------------------------------------------------

def get_track_info(track_id: int) -> dict:
    return _get("/info/", id=track_id)["data"]


def get_recommendations(track_id: int) -> list[dict]:
    items = _get("/recommendations/", id=track_id)["data"]["items"]
    return [e["track"] if isinstance(e, dict) and "track" in e else e for e in items]


def get_lyrics(track_id: int) -> dict | None:
    try:
        return _get("/lyrics/", id=track_id)["data"]
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Album / Artist
# ---------------------------------------------------------------------------

def get_album(album_id: int, limit: int = 100) -> dict:
    data = _get("/album/", id=album_id, limit=limit)["data"]
    # Unwrap items: each entry is {"item": {...}, "type": "track"}
    if "items" in data:
        data["items"] = [
            e["item"] if isinstance(e, dict) and "item" in e else e
            for e in data["items"]
        ]
    return data


def get_artist_tracks(artist_name: str, artist_id: int | None = None, limit: int = 50) -> list[dict]:
    """Search for tracks by artist name, filtered to only that artist's tracks."""
    data = _get("/search/", s=artist_name, limit=limit, offset=0)
    tracks = data["data"]["items"]
    if artist_id is not None:
        tracks = [t for t in tracks if t.get("artist", {}).get("id") == artist_id]
    else:
        name_lower = artist_name.lower()
        tracks = [
            t for t in tracks
            if name_lower in t.get("artist", {}).get("name", "").lower()
        ]
    return tracks


def get_artist_discography(artist_name: str, artist_id: int) -> dict[str, list[dict]]:
    """
    Return {"tracks": [...], "albums": [...], "eps_singles": [...]} for an artist.

    Strategy (API has no dedicated discography endpoint):
    1. Search tracks by artist name → collect top tracks + unique album stubs.
    2. Search albums by artist name → filter by artist_id, get type field.
    3. Merge both album sets, fetch full metadata for albums only found as stubs.
    4. Split into ALBUM vs EP/SINGLE buckets.
    """
    import json as _json

    # --- top tracks (paginate up to 4 pages to find more album IDs) ---
    tracks: list[dict] = []
    stub_ids: dict[int, dict] = {}
    for offset in range(0, 200, 50):
        page = _get("/search/", s=artist_name, limit=50, offset=offset)
        page_tracks = page["data"]["items"]
        for t in page_tracks:
            if t.get("artist", {}).get("id") == artist_id:
                tracks.append(t)
                al = t.get("album", {})
                if al.get("id"):
                    stub_ids[al["id"]] = al
        if not page_tracks:
            break

    # --- albums from direct album search (captures releases with artist name in title) ---
    known: dict[int, dict] = {}
    try:
        al_results = _get("/search/", al=artist_name, limit=50)["data"]["albums"]["items"]
        for a in al_results:
            arts = a.get("artists") or []
            if any(ar.get("id") == artist_id for ar in arts):
                known[a["id"]] = a
    except Exception:
        pass

    # Fetch metadata for stub albums not already in known
    for aid in stub_ids:
        if aid not in known:
            try:
                data = _get("/album/", id=aid, limit=1)["data"]
                known[aid] = data
            except Exception:
                # Use stub as fallback
                known[aid] = stub_ids[aid]

    albums: list[dict] = []
    eps_singles: list[dict] = []
    for a in sorted(known.values(), key=lambda x: x.get("releaseDate", "") or "", reverse=True):
        t = a.get("type", "")
        if t == "ALBUM":
            albums.append(a)
        else:
            eps_singles.append(a)

    return {"tracks": tracks, "albums": albums, "eps_singles": eps_singles}


def get_artist(artist_id: int) -> dict:
    return _get("/artist/", id=artist_id)["artist"]


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------

def _serve_dash_manifest(mpd_content: str) -> str:
    """
    Write an MPD string to a temp file, spin up a one-file HTTP server on
    localhost, and return the http://127.0.0.1:PORT/manifest.mpd URL.

    mpv fetches the MPD over HTTP so FFmpeg's DASH demuxer has unrestricted
    protocol access for the HTTPS segment URLs inside the manifest.
    """
    global _active_mpd_server, _active_mpd_tmpfile

    # Shut down the previous server/file if any
    if _active_mpd_server is not None:
        try:
            _active_mpd_server.shutdown()
        except Exception:
            pass
    if _active_mpd_tmpfile and os.path.exists(_active_mpd_tmpfile):
        try:
            os.unlink(_active_mpd_tmpfile)
        except Exception:
            pass

    tmp = tempfile.NamedTemporaryFile(suffix=".mpd", delete=False, mode="w", encoding="utf-8")
    tmp.write(mpd_content)
    tmp.close()
    mpd_path = tmp.name

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            with open(mpd_path, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "application/dash+xml")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args: object) -> None:
            pass  # suppress request logs

    server = socketserver.TCPServer(("127.0.0.1", 0), _Handler)
    server.allow_reuse_address = True
    port = server.server_address[1]

    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    _active_mpd_server = server
    _active_mpd_tmpfile = mpd_path

    return f"http://127.0.0.1:{port}/manifest.mpd"


def get_stream_url(track_id: int, quality: str = "HI_RES_LOSSLESS") -> str | None:
    """
    Resolve a direct playable URL from the /track/ manifest.

    The manifest is base64-encoded.  It's either:
      - A JSON object with a 'urls' list  (FLAC/direct)
      - An MPEG-DASH XML string (.mpd)
    Raises on failure so callers can surface the real error.
    """
    import json as _json

    try:
        data = _get("/track/", id=track_id, quality=quality)["data"]
    except Exception:
        data = _get("/track/", id=track_id, quality="LOSSLESS")["data"]

    manifest_b64 = data.get("manifest", "")
    if not manifest_b64:
        raise RuntimeError(f"No manifest in /track/ response for id={track_id}")

    raw = base64.b64decode(manifest_b64 + "==").decode("utf-8")

    # Try JSON first (direct FLAC streams)
    try:
        manifest_json = _json.loads(raw)
        urls = manifest_json.get("urls") or manifest_json.get("url")
        if isinstance(urls, list) and urls:
            return urls[0]
        if isinstance(urls, str):
            return urls
    except _json.JSONDecodeError:
        pass

    # DASH XML manifest — serve it via a local HTTP server so mpv can stream it
    # with full protocol access (the HTTPS segment URLs in the MPD won't be
    # blocked by FFmpeg's file-protocol whitelist).
    return _serve_dash_manifest(raw)


def format_duration(seconds: int) -> str:
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def cover_url(cover_id: str, size: int = 320) -> str:
    return f"https://resources.tidal.com/images/{cover_id.replace('-', '/')}/{size}x{size}.jpg"
