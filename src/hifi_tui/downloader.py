"""Download tracks, covers, lyrics, and CUE files from the HiFi API."""

from __future__ import annotations

import http.server
import re
import socketserver
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

import requests

from . import api

DOWNLOAD_DIR = Path.home() / "Music" / "hifi-tui"
_SIZE_RE = re.compile(r"size=\s*(\d+)kB")


@dataclass
class DownloadJob:
    id: str
    title: str
    status: str = "Queued"
    bytes_done: int = 0
    bytes_total: int = 0
    error: str = ""

    @property
    def progress_str(self) -> str:
        if self.bytes_total > 0:
            pct = self.bytes_done * 100 // self.bytes_total
            done_mb = self.bytes_done / 1_048_576
            total_mb = self.bytes_total / 1_048_576
            return f"{pct}%  {done_mb:.1f} / {total_mb:.1f} MB"
        if self.bytes_done > 0:
            return f"{self.bytes_done / 1_048_576:.1f} MB"
        if self.status == "Failed":
            return self.error[:60]
        return ""


def _sanitize(name: str) -> str:
    name = re.sub(r'[/\\]', '-', str(name))
    return re.sub(r'[<>:"|?*\x00-\x1f]', '_', name).strip(". ")


def _fetch_cover(cover_id: str) -> bytes | None:
    try:
        r = requests.get(api.cover_url(cover_id, 1280), timeout=15)
        r.raise_for_status()
        return r.content
    except Exception:
        return None


def _fetch_lrclib(track_data: dict) -> str:
    try:
        r = requests.get(
            "https://lrclib.net/api/get",
            params={
                "track_name": track_data.get("title", ""),
                "artist_name": track_data.get("artist", {}).get("name", ""),
                "album_name": track_data.get("album", {}).get("title", ""),
                "duration": track_data.get("duration", 0),
            },
            timeout=10,
        )
        if r.status_code == 200:
            return r.json().get("syncedLyrics") or ""
    except Exception:
        pass
    return ""


def _save_lyrics(track_id: int, base: Path, track_data: dict | None = None) -> None:
    tidal = api.get_lyrics(track_id)
    if tidal and tidal.get("subtitles"):
        base.with_suffix(".lrc").write_text(tidal["subtitles"], encoding="utf-8")
        return
    if track_data is None:
        return
    synced = _fetch_lrclib(track_data)
    if synced:
        base.with_suffix(".lrc").write_text(synced, encoding="utf-8")


def _embed_flac(path: Path, track: dict, cover: bytes | None) -> None:
    from mutagen.flac import FLAC, Picture

    audio = FLAC(path)
    audio["title"] = [track.get("title", "")]
    audio["artist"] = [track.get("artist", {}).get("name", "")]
    audio["albumartist"] = [track.get("artist", {}).get("name", "")]
    audio["album"] = [track.get("album", {}).get("title", "")]
    audio["tracknumber"] = [str(track.get("trackNumber", ""))]

    if disc := track.get("volumeNumber"):
        audio["discnumber"] = [str(disc)]
    if date := str(track.get("album", {}).get("releaseDate", "") or ""):
        audio["date"] = [date]
    if isrc := track.get("isrc"):
        audio["isrc"] = [isrc]
    if copyright_ := track.get("copyright"):
        audio["copyright"] = [copyright_]
    if bpm := track.get("bpm"):
        audio["bpm"] = [str(bpm)]

    if cover:
        pic = Picture()
        pic.type = 3
        pic.mime = "image/jpeg"
        pic.data = cover
        audio.add_picture(pic)

    audio.save()


def _embed_m4a(path: Path, track: dict, cover: bytes | None) -> None:
    from mutagen.mp4 import MP4, MP4Cover

    audio = MP4(path)
    audio["\xa9nam"] = [track.get("title", "")]
    audio["\xa9ART"] = [track.get("artist", {}).get("name", "")]
    audio["aART"] = [track.get("artist", {}).get("name", "")]
    audio["\xa9alb"] = [track.get("album", {}).get("title", "")]

    if tnum := track.get("trackNumber"):
        audio["trkn"] = [(int(tnum), 0)]
    if disc := track.get("volumeNumber"):
        audio["disk"] = [(int(disc), 0)]
    if date := str(track.get("album", {}).get("releaseDate", "") or ""):
        audio["\xa9day"] = [date]
    if isrc := track.get("isrc"):
        audio["----:com.apple.iTunes:ISRC"] = [isrc.encode()]
    if copyright_ := track.get("copyright"):
        audio["cprt"] = [copyright_]
    if bpm := track.get("bpm"):
        audio["tmpo"] = [int(bpm)]

    if cover:
        audio["covr"] = [MP4Cover(cover, imageformat=MP4Cover.FORMAT_JPEG)]

    audio.save()


def _download_direct(url: str, path: Path, job: DownloadJob | None = None) -> None:
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        if job:
            job.bytes_total = total
        done = 0
        with open(path, "wb") as f:
            for chunk in r.iter_content(chunk_size=65536):
                f.write(chunk)
                done += len(chunk)
                if job:
                    job.bytes_done = done


def _download_dash(mpd_xml: str, path: Path, job: DownloadJob | None = None) -> None:
    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            data = mpd_xml.encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/dash+xml")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *_) -> None:
            pass

    server = socketserver.TCPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    # FLAC-in-fMP4 DASH cannot be stream-copied to a .flac container; decode
    # and re-encode. For .m4a, try stream copy first and fall back to transcode.
    if path.suffix.lower() == ".flac":
        attempts = [["-vn", "-c:a", "flac"]]
    else:
        attempts = [["-vn", "-c:a", "copy"], ["-vn"]]

    try:
        stderr_lines: list[str] = []
        for extra in attempts:
            proc = subprocess.Popen(
                ["ffmpeg", "-y", "-i", f"http://127.0.0.1:{port}/manifest.mpd"] + extra + [str(path)],
                stderr=subprocess.PIPE, stdout=subprocess.DEVNULL,
                text=True, errors="replace",
            )
            stderr_lines = []
            for line in proc.stderr:
                stderr_lines.append(line.rstrip())
                if job and (m := _SIZE_RE.search(line)):
                    job.bytes_done = int(m.group(1)) * 1024
            proc.wait()
            if proc.returncode == 0:
                return
        meaningful = [ln for ln in stderr_lines if ln.strip()]
        raise RuntimeError("\n".join(meaningful[-3:]) if meaningful else "ffmpeg failed")
    finally:
        server.shutdown()


def download_track(
    track_data: dict,
    job: DownloadJob | None = None,
    dest_root: Path = DOWNLOAD_DIR,
) -> Path:
    track_id = track_data["id"]
    artist_name = track_data.get("artist", {}).get("name", "Unknown Artist")
    album_data = track_data.get("album", {})
    album_title = album_data.get("title", "Unknown Album")
    track_num = track_data.get("trackNumber", 0)
    title = track_data.get("title", "track")

    folder = dest_root / _sanitize(artist_name) / _sanitize(album_title)
    folder.mkdir(parents=True, exist_ok=True)
    base_name = f"{track_num:02d} - {_sanitize(title)}"

    if job:
        job.status = "Fetching"

    try:
        info = api.get_track_info(track_id)
        track_data = {**info, "album": {**album_data, **info.get("album", {})}}
        album_data = track_data.get("album", album_data)
    except Exception:
        pass

    kind, content, mime_type = api.get_track_manifest(track_id)
    ext = ".flac" if "flac" in mime_type.lower() else ".m4a"
    out = folder / f"{base_name}{ext}"

    if out.exists():
        if job:
            job.status = "Already exists"
        return out

    if job:
        job.status = "Downloading"

    if kind == "url":
        _download_direct(content, out, job=job)
    else:
        _download_dash(content, out, job=job)

    if job:
        job.status = "Tagging"

    cover: bytes | None = None
    if cover_id := album_data.get("cover"):
        cover_path = folder / "cover.jpg"
        if cover_path.exists():
            cover = cover_path.read_bytes()
        else:
            cover = _fetch_cover(cover_id)
            if cover:
                cover_path.write_bytes(cover)

    tag_error = ""
    try:
        if ext == ".flac":
            _embed_flac(out, track_data, cover)
        else:
            _embed_m4a(out, track_data, cover)
    except Exception as e:
        tag_error = str(e)

    try:
        _save_lyrics(track_id, folder / base_name, track_data)
    except Exception:
        pass

    if job:
        job.status = "Done"
        if tag_error:
            job.error = f"Tagging failed: {tag_error}"

    return out


def download_album(
    album_data: dict,
    tracks: list[dict],
    jobs: list[DownloadJob] | None = None,
    dest_root: Path = DOWNLOAD_DIR,
) -> Path:
    artists = album_data.get("artists") or []
    artist_name = (
        artists[0].get("name", "")
        if artists
        else album_data.get("artist", {}).get("name", "Unknown Artist")
    )
    album_title = album_data.get("title", "Unknown Album")
    folder = dest_root / _sanitize(artist_name) / _sanitize(album_title)

    results: list[tuple[dict, Path | None]] = []
    for i, t in enumerate(tracks):
        if not t.get("album"):
            t = {**t, "album": album_data}
        job = jobs[i] if jobs and i < len(jobs) else None
        try:
            path = download_track(t, job=job, dest_root=dest_root)
            results.append((t, path))
        except Exception as e:
            if job:
                job.status = "Failed"
                job.error = str(e)
            results.append((t, None))

    _write_cue(album_data, results, artist_name, folder)
    return folder


def _write_cue(
    album_data: dict,
    results: list[tuple[dict, Path | None]],
    artist_name: str,
    folder: Path,
) -> None:
    album_title = album_data.get("title", "Album")
    lines = [f'PERFORMER "{artist_name}"', f'TITLE "{album_title}"']
    for i, (t, path) in enumerate(results, 1):
        filename = (
            path.name if path
            else f"{t.get('trackNumber', i):02d} - {_sanitize(t.get('title', ''))}.flac"
        )
        lines += [
            f'FILE "{filename}" WAVE',
            f"  TRACK {i:02d} AUDIO",
            f'    TITLE "{t.get("title", "")}"',
            f'    PERFORMER "{t.get("artist", {}).get("name", artist_name)}"',
            f"    INDEX 01 00:00:00",
        ]
    (folder / f"{_sanitize(album_title)}.cue").write_text("\n".join(lines), encoding="utf-8")
