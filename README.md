# hifi-tui

A TUI for browsing and streaming music via the [HiFi API](https://github.com/binimum/hifi-api) (a Tidal proxy). Runs on Linux and Windows.

## Features

- **Search** tracks, albums, and artists
- **Stream** lossless and hi-res audio via mpv
- **Queue** management with reordering and shuffle/repeat modes
- **Recommendations** based on currently playing track
- **Playlists** — create, rename, delete, and reorder tracks within playlists
- **Downloads** — lossless FLAC with full metadata, cover art, synced lyrics, and CUE files
- **Track metadata** modal showing quality, BPM, key, ISRC, and more
- **Last.fm scrobbling** with now-playing and scrobble support
- **Command palette** (`Ctrl+P`) listing all keybindings

## Requirements

- Python 3.10+
- mpv
- ffmpeg (required for HiRes DASH downloads)

## Setup

### Linux

```bash
python -m venv venv
venv/bin/pip install textual requests mutagen
```

### Windows

```powershell
python -m venv venv
venv\Scripts\pip install textual requests mutagen
```

mpv must be installed and available on your `PATH`:

```powershell
winget install mpv
winget install Gyan.FFmpeg
```

## Configuration

The API base URL is set in `src/hifi_tui/api.py`. Change `BASE_URL` to point to your own HiFi API instance.

## Run

### Linux

```bash
./run.sh
```

### Windows

```powershell
.\run.bat
```

## Keybindings

Press `Ctrl+P` in the app to open the command palette with a searchable list of all keybindings.

### Playback

| Key | Action |
|-----|--------|
| `Space` | Pause / Resume |
| `n` / `p` | Next / Previous track |
| `+` / `-` | Volume up / down |
| `Left` / `Right` | Seek ±10s |
| `s` | Toggle shuffle |
| `r` | Cycle repeat (off → queue → track) |

### Navigation

| Key | Action |
|-----|--------|
| `Enter` | Play track / open album or artist |
| `Escape` | Go back |
| `q` | Quit |
| `Ctrl+P` | Command palette (keybindings) |

### Tracks

| Key | Action |
|-----|--------|
| `a` | Add selected track / album to queue |
| `l` | Add selected track to playlist |
| `i` | Show track metadata |
| `Ctrl+L` | Add currently playing track to playlist |
| `Ctrl+Shift+I` | Show currently playing track metadata |

### Search tab

| Key | Action |
|-----|--------|
| `F2` | Switch to Tracks search mode |
| `F3` | Switch to Albums search mode |
| `F4` | Switch to Artists search mode |

### Queue tab

| Key | Action |
|-----|--------|
| `Delete` | Remove selected track |
| `Ctrl+Up` / `Ctrl+Down` | Move track up / down |

### Playlists tab

| Key | Action |
|-----|--------|
| `n` | New playlist |
| `Ctrl+R` | Rename selected playlist |
| `Delete` | Delete selected playlist |

### Inside a playlist

| Key | Action |
|-----|--------|
| `Ctrl+Up` / `Ctrl+Down` | Move track up / down |
| `Delete` | Remove track from playlist |

## Downloads

Press `d` on any track or album to download it. Progress is shown in the **Downloads** tab.

Each download includes:
- Audio file — FLAC (lossless and HiRes streams)
- Cover art (`cover.jpg`)
- Synced lyrics (`.lrc`) when available, sourced from Tidal or [lrclib.net](https://lrclib.net)
- Embedded metadata: title, artist, album, track number, disc number, date, ISRC, copyright, BPM, cover art
- CUE file (album downloads only)

The default download folder is `~/Music/hifi-tui/`. You can change it in the **Settings** tab.

| Key | Action |
|-----|--------|
| `d` | Download selected track / album |
| `Ctrl+D` | Download currently playing track |
| `Ctrl+D` (Album screen) | Download full album |

## Last.fm

Open the **Settings** tab to configure Last.fm scrobbling. You'll need a free API key from [last.fm/api/account/create](https://www.last.fm/api/account/create).

## Credits

Powered by [hifi-api](https://github.com/binimum/hifi-api) by binimum.

Thanks to [monochrome](https://github.com/monochrome-music/monochrome) for introducing me to the HiFi API.
