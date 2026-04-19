"""mpv-based audio player with IPC socket control."""

from __future__ import annotations

import json
import os
import random
import socket
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable

_IS_WINDOWS = sys.platform == "win32"

if _IS_WINDOWS:
    import ctypes
    import ctypes.wintypes as _wt

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _GENERIC_RW = 0xC0000000  # GENERIC_READ | GENERIC_WRITE
    _OPEN_EXISTING = 3
    _INVALID_HANDLE = ctypes.c_void_p(-1).value

    class _WinPipe:
        """Named-pipe wrapper with the same sendall/recv/close interface as a socket."""

        def __init__(self, path: str):
            self._h = _k32.CreateFileW(path, _GENERIC_RW, 0, None, _OPEN_EXISTING, 0, None)
            if self._h == _INVALID_HANDLE:
                raise OSError(ctypes.get_last_error(), f"pipe open failed: {path}")

        def sendall(self, data: bytes) -> None:
            while data:
                n = _wt.DWORD(0)
                if not _k32.WriteFile(self._h, data, len(data), ctypes.byref(n), None):
                    raise OSError(ctypes.get_last_error(), "pipe write failed")
                data = data[n.value:]

        def recv(self, size: int) -> bytes:
            avail = _wt.DWORD(0)
            _k32.PeekNamedPipe(self._h, None, 0, None, ctypes.byref(avail), None)
            if not avail.value:
                raise BlockingIOError()
            buf = ctypes.create_string_buffer(min(size, avail.value))
            n = _wt.DWORD(0)
            _k32.ReadFile(self._h, buf, len(buf), ctypes.byref(n), None)
            return buf.raw[:n.value]

        def close(self) -> None:
            if self._h and self._h != _INVALID_HANDLE:
                _k32.CloseHandle(self._h)
                self._h = None


class RepeatMode(Enum):
    NONE = auto()
    QUEUE = auto()
    TRACK = auto()


@dataclass
class TrackInfo:
    track_id: int
    title: str
    artist: str
    album: str
    duration: int  # seconds
    quality: str = ""


@dataclass
class PlayerState:
    track: TrackInfo | None = None
    playing: bool = False
    position: float = 0.0   # seconds elapsed
    duration: float = 0.0
    volume: int = 100
    queue: list[TrackInfo] = field(default_factory=list)
    queue_index: int = -1
    queue_version: int = 0   # incremented on every queue mutation
    shuffle: bool = False
    repeat: RepeatMode = RepeatMode.NONE


class Player:
    """
    Wraps mpv via JSON IPC socket.
    Callbacks fire on a background thread.
    """

    def __init__(self, on_state_change: Callable[[PlayerState], None] | None = None):
        self._proc: subprocess.Popen | None = None
        if _IS_WINDOWS:
            _uid = os.urandom(4).hex()
            # mpv on Windows requires the full \\.\pipe\ path for --input-ipc-server
            self._sock_path = f"\\\\.\\pipe\\hifi-mpv-{_uid}"
            self._pipe_path = self._sock_path
        else:
            self._sock_path = os.path.join(tempfile.mkdtemp(), "hifi-mpv.sock")
            self._pipe_path = self._sock_path
        self._sock = None  # socket.socket on Unix, _WinPipe on Windows
        self._lock = threading.Lock()
        self._state = PlayerState()
        self._on_state_change = on_state_change
        self._poller: threading.Thread | None = None
        self._running = False
        self._url_loader: Callable[[int], str | None] | None = None
        self._original_queue: list[TrackInfo] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_url_loader(self, loader: Callable[[int], str | None]) -> None:
        """Provide a callable that resolves a track_id → stream URL."""
        self._url_loader = loader

    def play(self, track: TrackInfo, url: str) -> None:
        self._start_mpv_if_needed()
        self._state.track = track
        self._state.playing = True
        self._state.position = 0.0
        self._state.duration = float(track.duration)
        self._send_command(["loadfile", url, "replace"])
        self._notify()

    def pause_toggle(self) -> None:
        self._send_command(["cycle", "pause"])

    def seek(self, seconds: float) -> None:
        self._send_command(["seek", seconds, "absolute"])

    def seek_relative(self, delta: float) -> None:
        self._send_command(["seek", delta, "relative"])

    def set_volume(self, vol: int) -> None:
        vol = max(0, min(200, vol))
        self._state.volume = vol
        self._send_command(["set_property", "volume", vol])
        self._notify()

    def stop(self) -> None:
        self._send_command(["stop"])
        self._state.playing = False
        self._notify()

    def quit(self) -> None:
        self._running = False
        if self._proc and self._proc.poll() is None:
            try:
                self._send_command(["quit"])
            except Exception:
                pass
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None
        self._close_socket()

    @property
    def state(self) -> PlayerState:
        return self._state

    # ------------------------------------------------------------------
    # Queue helpers
    # ------------------------------------------------------------------

    def set_queue(self, tracks: list[TrackInfo], start_index: int = 0) -> None:
        self._state.queue = list(tracks)
        self._state.queue_index = start_index
        self._state.queue_version += 1
        self._notify()

    def enqueue(self, track: TrackInfo) -> None:
        self._state.queue.append(track)
        self._state.queue_version += 1
        self._notify()

    def enqueue_many(self, tracks: list[TrackInfo]) -> None:
        self._state.queue.extend(tracks)
        self._state.queue_version += 1
        self._notify()

    def dequeue(self, idx: int) -> None:
        q = self._state.queue
        if not (0 <= idx < len(q)):
            return
        q.pop(idx)
        if idx < self._state.queue_index:
            self._state.queue_index -= 1
        elif idx == self._state.queue_index:
            self._state.queue_index = min(self._state.queue_index, len(q) - 1)
        self._state.queue_version += 1
        self._notify()

    def move_in_queue(self, from_idx: int, to_idx: int) -> None:
        q = self._state.queue
        if not (0 <= from_idx < len(q) and 0 <= to_idx < len(q)):
            return
        track = q.pop(from_idx)
        q.insert(to_idx, track)
        qi = self._state.queue_index
        if from_idx == qi:
            self._state.queue_index = to_idx
        elif from_idx < qi <= to_idx:
            self._state.queue_index -= 1
        elif to_idx <= qi < from_idx:
            self._state.queue_index += 1
        self._state.queue_version += 1
        self._notify()

    def play_from_queue(self, index: int) -> None:
        if not self._state.queue or not (0 <= index < len(self._state.queue)):
            return
        track = self._state.queue[index]
        self._state.queue_index = index
        if self._url_loader:
            url = self._url_loader(track.track_id)
            if url:
                self.play(track, url)

    def next_track(self) -> None:
        idx = self._state.queue_index + 1
        if idx < len(self._state.queue):
            self.play_from_queue(idx)

    def prev_track(self) -> None:
        idx = self._state.queue_index - 1
        if idx >= 0:
            self.play_from_queue(idx)

    def toggle_shuffle(self) -> None:
        if not self._state.shuffle:
            # Save original order, shuffle queue, keep current track at front
            self._original_queue = list(self._state.queue)
            current = (
                self._state.queue[self._state.queue_index]
                if 0 <= self._state.queue_index < len(self._state.queue)
                else None
            )
            rest = [t for t in self._state.queue if t is not current]
            random.shuffle(rest)
            self._state.queue = ([current] + rest) if current else rest
            self._state.queue_index = 0 if current else -1
            self._state.shuffle = True
        else:
            # Restore original order, find current track in it
            current = (
                self._state.queue[self._state.queue_index]
                if 0 <= self._state.queue_index < len(self._state.queue)
                else None
            )
            self._state.queue = list(self._original_queue)
            self._original_queue = []
            if current:
                try:
                    self._state.queue_index = next(
                        i for i, t in enumerate(self._state.queue)
                        if t.track_id == current.track_id
                    )
                except StopIteration:
                    self._state.queue_index = 0
            self._state.shuffle = False
        self._state.queue_version += 1
        self._notify()

    def cycle_repeat(self) -> None:
        modes = [RepeatMode.NONE, RepeatMode.QUEUE, RepeatMode.TRACK]
        current_idx = modes.index(self._state.repeat)
        self._state.repeat = modes[(current_idx + 1) % len(modes)]
        self._notify()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _start_mpv_if_needed(self) -> None:
        if self._proc and self._proc.poll() is None:
            return
        args = [
            "mpv",
            "--no-video",
            "--idle",
            f"--input-ipc-server={self._sock_path}",
            *([] if _IS_WINDOWS else ["--really-quiet"]),
        ]
        if _IS_WINDOWS:
            import tempfile as _tmp
            self._mpv_log = os.path.join(_tmp.gettempdir(), "hifi-mpv.log")
            _logf = open(self._mpv_log, "w")
        else:
            _logf = subprocess.DEVNULL
        self._proc = subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=_logf,
            stderr=_logf,
        )
        if _IS_WINDOWS:
            time.sleep(0.5)
            if self._proc.poll() is not None:
                raise RuntimeError(
                    f"mpv exited immediately (code {self._proc.returncode}). "
                    f"Check log: {self._mpv_log}"
                )
        # Wait for IPC endpoint to appear
        for _ in range(50):
            if _IS_WINDOWS:
                try:
                    _WinPipe(self._pipe_path).close()
                    break
                except OSError:
                    pass
            else:
                if os.path.exists(self._sock_path):
                    break
            time.sleep(0.1)
        self._connect_socket()
        self._running = True
        self._poller = threading.Thread(target=self._poll_loop, daemon=True)
        self._poller.start()

    def _connect_socket(self) -> None:
        if _IS_WINDOWS:
            self._sock = _WinPipe(self._pipe_path)
        else:
            self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self._sock.connect(self._sock_path)
            self._sock.setblocking(False)

    def _close_socket(self) -> None:
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    def _send_command(self, cmd: list) -> None:
        if not self._sock:
            return
        msg = json.dumps({"command": cmd}) + "\n"
        with self._lock:
            try:
                self._sock.sendall(msg.encode())
            except Exception:
                pass

    def _get_property(self, prop: str) -> object:
        if not self._sock:
            return None
        request_id = int(time.time() * 1000) % 100000
        msg = json.dumps({"command": ["get_property", prop], "request_id": request_id}) + "\n"
        with self._lock:
            try:
                self._sock.sendall(msg.encode())
            except Exception:
                return None
        # Read response with timeout
        deadline = time.time() + 0.5
        buf = b""
        while time.time() < deadline:
            try:
                chunk = self._sock.recv(4096)
                if chunk:
                    buf += chunk
                    for line in buf.split(b"\n"):
                        if not line:
                            continue
                        try:
                            obj = json.loads(line)
                            if obj.get("request_id") == request_id:
                                return obj.get("data")
                        except Exception:
                            pass
            except BlockingIOError:
                time.sleep(0.05)
        return None

    def _poll_loop(self) -> None:
        """Background thread: polls mpv state every second."""
        _was_playing = False
        while self._running:
            try:
                pause = self._get_property("pause")
                pos = self._get_property("time-pos")
                dur = self._get_property("duration")
                vol = self._get_property("volume")
                idle = self._get_property("idle-active")

                changed = False
                if pause is not None:
                    new_playing = not bool(pause)
                    if new_playing != self._state.playing:
                        self._state.playing = new_playing
                        changed = True
                if isinstance(pos, (int, float)):
                    self._state.position = float(pos)
                    changed = True
                if isinstance(dur, (int, float)):
                    self._state.duration = float(dur)
                    changed = True
                if isinstance(vol, (int, float)):
                    self._state.volume = int(vol)
                    changed = True

                # Auto-advance: mpv went idle after a track was playing
                if idle and _was_playing and self._state.track is not None:
                    repeat = self._state.repeat
                    if repeat == RepeatMode.TRACK:
                        self.play_from_queue(self._state.queue_index)
                    elif repeat == RepeatMode.QUEUE:
                        next_idx = (self._state.queue_index + 1) % max(len(self._state.queue), 1)
                        self.play_from_queue(next_idx)
                    else:
                        next_idx = self._state.queue_index + 1
                        if next_idx < len(self._state.queue):
                            self.play_from_queue(next_idx)
                        else:
                            self._state.playing = False
                            self._state.position = 0.0
                            changed = True

                _was_playing = self._state.playing and not bool(idle)

                if changed:
                    self._notify()
            except Exception:
                pass
            time.sleep(0.5)

    def _notify(self) -> None:
        if self._on_state_change:
            try:
                self._on_state_change(self._state)
            except Exception:
                pass
