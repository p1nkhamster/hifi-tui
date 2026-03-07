# hifi-tui

A Linux TUI for browsing and streaming music via the [HiFi API](https://github.com/binimum/hifi-api) (a Tidal proxy).

## Requirements

- Python 3.10+
- mpv (system package)

## Setup

```bash
python -m venv venv
venv/bin/pip install textual requests
```

## Configuration

The API base URL is set in `src/hifi_tui/api.py`. Change `BASE_URL` to point to your own HiFi API instance.

## Run

```bash
./run.sh
```

## Keybindings

| Key | Action |
|-----|--------|
| Enter | Play track / open album or artist |
| Space | Pause / Resume |
| n / p | Next / Previous track |
| a | Add selected track or album to queue |
| s | Toggle shuffle |
| r | Cycle repeat (off → repeat queue → repeat track) |
| + / - | Volume up / down |
| ← / → | Seek ±10s |
| F2 | Search mode: Tracks |
| F3 | Search mode: Albums |
| F4 | Search mode: Artists |
| Delete | Remove track from queue |
| Ctrl+↑ / Ctrl+↓ | Move track up / down in queue |
| Escape | Go back (album / artist screens) |
| q | Quit |

## Credits

Powered by [hifi-api](https://github.com/binimum/hifi-api) by binimum.

Thanks to [monochrome](https://github.com/monochrome-music/monochrome) for introducing me to the HiFi API.
