"""Persistent app configuration."""

from __future__ import annotations

import json
from pathlib import Path

from .downloader import DOWNLOAD_DIR

CONFIG_PATH = Path.home() / ".local" / "share" / "hifi-tui" / "config.json"


def load() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text())
    except Exception:
        return {}


def save(cfg: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))


def get_download_dir() -> Path:
    cfg = load()
    raw = cfg.get("download_dir")
    return Path(raw) if raw else DOWNLOAD_DIR


def set_download_dir(path: Path) -> None:
    cfg = load()
    cfg["download_dir"] = str(path)
    save(cfg)
