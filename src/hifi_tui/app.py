"""HiFi TUI — main Textual application."""

from __future__ import annotations

import threading
from typing import ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Vertical
from textual.css.query import NoMatches
from textual.reactive import reactive
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    Static,
    TabbedContent,
    TabPane,
)

from . import api
from .player import Player, PlayerState, RepeatMode, TrackInfo


# ---------------------------------------------------------------------------
# Now-Playing bar
# ---------------------------------------------------------------------------

class NowPlayingBar(Static):
    """A persistent bar at the bottom showing current track + progress."""

    DEFAULT_CSS = """
    NowPlayingBar {
        height: 5;
        background: $panel;
        border-top: solid $accent;
        padding: 0 1;
    }
    NowPlayingBar #np-title {
        color: $accent;
        text-style: bold;
    }
    NowPlayingBar #np-meta {
        color: $text-muted;
    }
    NowPlayingBar #np-time {
        color: $text-muted;
    }
    """

    def compose(self) -> ComposeResult:
        yield Label("No track loaded", id="np-title")
        yield Label("", id="np-meta")
        yield Label("", id="np-time")

    def update_state(self, state: PlayerState) -> None:
        pos_str = api.format_duration(int(state.position))
        dur_str = api.format_duration(int(state.duration))
        if state.track:
            status = "▶" if state.playing else "⏸"
            self.query_one("#np-title", Label).update(
                f"{status}  {state.track.title}"
            )
            self.query_one("#np-meta", Label).update(
                f"    {state.track.artist}  —  {state.track.album}"
            )
        else:
            self.query_one("#np-title", Label).update("No track loaded")
            self.query_one("#np-meta", Label).update("")
        shuffle_icon = "(s)" if state.shuffle else ""
        repeat_icon = {"NONE": "", "QUEUE": "(r-q)", "TRACK": "(r-t)"}[state.repeat.name]
        indicators = " ".join(x for x in [shuffle_icon, repeat_icon] if x)
        extras = f"  {indicators}" if indicators else ""
        self.query_one("#np-time", Label).update(
            f"    {pos_str} / {dur_str}    Vol: {state.volume}{extras}"
        )


# ---------------------------------------------------------------------------
# Search pane
# ---------------------------------------------------------------------------

QUALITY_LABEL = {
    "HI_RES_LOSSLESS": "HiRes",
    "LOSSLESS": "FLAC",
    "HIGH": "AAC",
    "LOW": "Low",
}


def _quality_label(item: dict) -> str:
    """Return the best available quality label for a track or album dict."""
    tags = item.get("mediaMetadata", {}).get("tags", [])
    if "HIRES_LOSSLESS" in tags:
        return "HiRes"
    q = item.get("audioQuality", "")
    return QUALITY_LABEL.get(q, q or "?")


class SearchPane(Container):
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("a", "add_to_queue", "Add to Queue"),
    ]

    DEFAULT_CSS = """
    SearchPane {
        height: 1fr;
    }
    SearchPane Input {
        margin: 1 0 0 0;
    }
    SearchPane DataTable {
        height: 1fr;
        margin-top: 1;
    }
    SearchPane #search-status {
        color: $text-muted;
        margin: 0 1;
    }
    """

    def __init__(self, player: Player, **kwargs):
        super().__init__(**kwargs)
        self._player = player
        self._results: list[dict] = []
        self._mode = "tracks"  # tracks | albums | artists

    def compose(self) -> ComposeResult:
        yield Input(placeholder="Search… (Enter to search, Tab to switch mode)", id="search-input")
        yield Label("Mode: Tracks  |  F2=Tracks  F3=Albums  F4=Artists", id="search-status")
        yield DataTable(id="search-table", cursor_type="row", zebra_stripes=True)

    def on_mount(self) -> None:
        self._init_table()

    def _init_table(self) -> None:
        table = self.query_one("#search-table", DataTable)
        table.clear(columns=True)
        if self._mode == "tracks":
            table.add_columns("Title", "Artist", "Album", "Quality", "Duration")
        elif self._mode == "albums":
            table.add_columns("Title", "Artist", "Tracks", "Year")
        elif self._mode == "artists":
            table.add_columns("Name", "Popularity")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        query = event.value.strip()
        if query:
            self._do_search(query)

    def _do_search(self, query: str) -> None:
        self.query_one("#search-status", Label).update("Searching…")
        self._results = []

        def _run():
            try:
                if self._mode == "tracks":
                    results = api.search_tracks(query)
                elif self._mode == "albums":
                    results = api.search_albums(query)
                else:
                    results = api.search_artists(query)
                self.app.call_from_thread(self._populate, results)
            except Exception as e:
                self.app.call_from_thread(
                    self.query_one("#search-status", Label).update,
                    f"Error: {e}"
                )

        threading.Thread(target=_run, daemon=True).start()

    def _populate(self, results: list[dict]) -> None:
        self._results = results
        table = self.query_one("#search-table", DataTable)
        table.clear()
        if self._mode == "tracks":
            for r in results:
                qlabel = _quality_label(r)
                artist = r.get("artist", {}).get("name", "?")
                album = r.get("album", {}).get("title", "?")
                dur = api.format_duration(r.get("duration", 0))
                table.add_row(r["title"], artist, album, qlabel, dur)
        elif self._mode == "albums":
            for r in results:
                artists = r.get("artists") or []
                artist = artists[0].get("name", "?") if artists else r.get("artist", {}).get("name", "?")
                table.add_row(
                    r.get("title", "?"),
                    artist,
                    str(r.get("numberOfTracks", "?")),
                    str(r.get("releaseDate", "?"))[:4],
                )
        elif self._mode == "artists":
            for r in results:
                table.add_row(r.get("name", "?"), str(r.get("popularity", "?")))

        mode_label = self._mode.capitalize()
        self.query_one("#search-status", Label).update(
            f"Mode: {mode_label}  |  {len(results)} results  |  F2=Tracks  F3=Albums  F4=Artists"
        )

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        idx = event.cursor_row
        if not self._results or idx >= len(self._results):
            return
        item = self._results[idx]
        if self._mode == "tracks":
            self._play_track(item)
        elif self._mode == "albums":
            self.app.push_screen(AlbumScreen(item["id"], item.get("title", "Album"), self._player))
        elif self._mode == "artists":
            self.app.push_screen(ArtistScreen(item["id"], item.get("name", "Artist"), self._player))

    def _play_track(self, track_data: dict) -> None:
        app: HiFiApp = self.app  # type: ignore
        app.play_track(track_data)

    def action_add_to_queue(self) -> None:
        table = self.query_one("#search-table", DataTable)
        idx = table.cursor_row
        if not self._results or idx >= len(self._results):
            return
        item = self._results[idx]
        app: HiFiApp = self.app  # type: ignore
        if self._mode == "tracks":
            self._player.enqueue(_track_info(item))
            app.notify(f"Added to queue: {item.get('title', '?')}")
        elif self._mode == "albums":
            title = item.get("title", "Album")
            app.notify(f"Adding album to queue: {title}…")
            def _load(album_id=item["id"], album_title=title):
                try:
                    data = api.get_album(album_id)
                    tracks = [_track_info(t) for t in data.get("items", [])]
                    self._player.enqueue_many(tracks)
                    app.call_from_thread(app.notify, f"Added {len(tracks)} tracks from '{album_title}'")
                except Exception as e:
                    app.call_from_thread(app.notify, f"Failed to add album: {e}", severity="error")
            threading.Thread(target=_load, daemon=True).start()

    def set_mode(self, mode: str) -> None:
        self._mode = mode
        self._init_table()
        self._results = []
        mode_label = mode.capitalize()
        self.query_one("#search-status", Label).update(
            f"Mode: {mode_label}  |  F2=Tracks  F3=Albums  F4=Artists"
        )
        self.query_one("#search-input", Input).focus()


# ---------------------------------------------------------------------------
# Album screen (pushed modal-style)
# ---------------------------------------------------------------------------

from textual.screen import Screen


class AlbumScreen(Screen):
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("enter", "play_selected", "Play"),
        Binding("a", "add_to_queue", "Add to Queue"),
    ]

    DEFAULT_CSS = """
    AlbumScreen {
        background: $surface;
    }
    AlbumScreen DataTable {
        height: 1fr;
    }
    AlbumScreen #album-header {
        text-style: bold;
        color: $accent;
        margin: 1;
    }
    """

    def __init__(self, album_id: int, album_title: str, player: Player):
        super().__init__()
        self._album_id = album_id
        self._album_title = album_title
        self._player = player
        self._tracks: list[dict] = []

    def compose(self) -> ComposeResult:
        yield Label(f"Album: {self._album_title}", id="album-header")
        yield DataTable(id="album-table", cursor_type="row", zebra_stripes=True)
        yield NowPlayingBar(id="now-playing")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one(NowPlayingBar).update_state(self._player.state)
        table = self.query_one("#album-table", DataTable)
        table.add_columns("#", "Title", "Artist", "Quality", "Duration")
        threading.Thread(target=self._load, daemon=True).start()

    def _load(self) -> None:
        try:
            data = api.get_album(self._album_id)
            tracks = data.get("items", [])
            self.app.call_from_thread(self._populate, tracks)
        except Exception as e:
            self.app.call_from_thread(
                self.query_one("#album-header", Label).update,
                f"Error loading album: {e}"
            )

    def _populate(self, tracks: list[dict]) -> None:
        self._tracks = tracks
        table = self.query_one("#album-table", DataTable)
        for t in tracks:
            num = str(t.get("trackNumber", "?"))
            title = t.get("title", "?")
            artist = t.get("artist", {}).get("name", "?")
            qlabel = _quality_label(t)
            dur = api.format_duration(t.get("duration", 0))
            table.add_row(num, title, artist, qlabel, dur)
        # Set queue
        self._player.set_queue(
            [_track_info(t) for t in tracks], 0
        )
        self._player.set_url_loader(
            lambda tid: api.get_stream_url(tid)
        )

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        idx = event.cursor_row
        if idx < len(self._tracks):
            self.app.play_track(self._tracks[idx])  # type: ignore
            self._player.set_queue([_track_info(t) for t in self._tracks], idx)

    def action_play_selected(self) -> None:
        table = self.query_one("#album-table", DataTable)
        self.on_data_table_row_selected(
            DataTable.RowSelected(table, table.cursor_row, table.get_row_at(table.cursor_row), None)  # type: ignore
        )

    def action_add_to_queue(self) -> None:
        table = self.query_one("#album-table", DataTable)
        idx = table.cursor_row
        if idx < len(self._tracks):
            t = self._tracks[idx]
            self._player.enqueue(_track_info(t))
            self.app.notify(f"Added to queue: {t.get('title', '?')}")  # type: ignore


class ArtistScreen(Screen):
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("a", "add_to_queue", "Add to Queue"),
    ]

    DEFAULT_CSS = """
    ArtistScreen {
        background: $surface;
    }
    ArtistScreen #artist-header {
        text-style: bold;
        color: $accent;
        margin: 1 1 0 1;
    }
    ArtistScreen #artist-loading {
        color: $text-muted;
        margin: 0 1 1 1;
    }
    ArtistScreen TabbedContent {
        height: 1fr;
    }
    ArtistScreen DataTable {
        height: 1fr;
    }
    """

    def __init__(self, artist_id: int, artist_name: str, player: Player):
        super().__init__()
        self._artist_id = artist_id
        self._artist_name = artist_name
        self._player = player
        self._tracks: list[dict] = []
        self._albums: list[dict] = []
        self._eps_singles: list[dict] = []

    def compose(self) -> ComposeResult:
        yield Label(f"Artist: {self._artist_name}", id="artist-header")
        yield Label("Loading…", id="artist-loading")
        with TabbedContent(id="artist-tabs"):
            with TabPane("Top Tracks", id="tab-tracks"):
                yield DataTable(id="tracks-table", cursor_type="row", zebra_stripes=True)
            with TabPane("Albums", id="tab-albums"):
                yield DataTable(id="albums-table", cursor_type="row", zebra_stripes=True)
            with TabPane("EP & Singles", id="tab-eps"):
                yield DataTable(id="eps-table", cursor_type="row", zebra_stripes=True)
        yield NowPlayingBar(id="now-playing")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one(NowPlayingBar).update_state(self._player.state)
        self.query_one("#tracks-table", DataTable).add_columns("Title", "Artist", "Quality", "Duration")
        self.query_one("#albums-table", DataTable).add_columns("Title", "Tracks", "Year")
        self.query_one("#eps-table", DataTable).add_columns("Title", "Tracks", "Year")
        threading.Thread(target=self._load, daemon=True).start()

    def _load(self) -> None:
        try:
            disco = api.get_artist_discography(self._artist_name, self._artist_id)
            self.app.call_from_thread(self._populate, disco)
        except Exception as e:
            self.app.call_from_thread(
                self.query_one("#artist-loading", Label).update,
                f"Error: {e}"
            )

    def _populate(self, disco: dict) -> None:
        self._tracks = disco["tracks"]
        self._albums = disco["albums"]
        self._eps_singles = disco["eps_singles"]

        tracks_table = self.query_one("#tracks-table", DataTable)
        for t in self._tracks:
            title = t.get("title", "?")
            artist = t.get("artist", {}).get("name", "?")
            qlabel = _quality_label(t)
            dur = api.format_duration(t.get("duration", 0))
            tracks_table.add_row(title, artist, qlabel, dur)

        albums_table = self.query_one("#albums-table", DataTable)
        for a in self._albums:
            albums_table.add_row(
                a.get("title", "?"),
                str(a.get("numberOfTracks", "?")),
                str(a.get("releaseDate", "?"))[:4],
            )

        eps_table = self.query_one("#eps-table", DataTable)
        for a in self._eps_singles:
            eps_table.add_row(
                a.get("title", "?"),
                str(a.get("numberOfTracks", "?")),
                str(a.get("releaseDate", "?"))[:4],
            )

        counts = (
            f"{len(self._tracks)} tracks  |  "
            f"{len(self._albums)} albums  |  "
            f"{len(self._eps_singles)} EP/singles"
        )
        self.query_one("#artist-loading", Label).update(counts)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        idx = event.cursor_row
        table_id = event.data_table.id

        if table_id == "tracks-table":
            if idx < len(self._tracks):
                self.app.play_track(self._tracks[idx])  # type: ignore
                self._player.set_queue([_track_info(t) for t in self._tracks], idx)

        elif table_id in ("albums-table", "eps-table"):
            lst = self._albums if table_id == "albums-table" else self._eps_singles
            if idx < len(lst):
                album = lst[idx]
                self.app.push_screen(  # type: ignore
                    AlbumScreen(album["id"], album.get("title", "Album"), self._player)
                )

    def action_add_to_queue(self) -> None:
        app: HiFiApp = self.app  # type: ignore
        try:
            active_tab = self.query_one("#artist-tabs", TabbedContent).active
        except NoMatches:
            return
        if active_tab == "tab-tracks":
            table = self.query_one("#tracks-table", DataTable)
            idx = table.cursor_row
            if idx < len(self._tracks):
                t = self._tracks[idx]
                self._player.enqueue(_track_info(t))
                app.notify(f"Added to queue: {t.get('title', '?')}")
        elif active_tab in ("tab-albums", "tab-eps"):
            lst = self._albums if active_tab == "tab-albums" else self._eps_singles
            table_id = "albums-table" if active_tab == "tab-albums" else "eps-table"
            idx = self.query_one(f"#{table_id}", DataTable).cursor_row
            if idx < len(lst):
                album = lst[idx]
                title = album.get("title", "Album")
                app.notify(f"Adding '{title}' to queue…")
                def _load(aid=album["id"], atitle=title):
                    try:
                        data = api.get_album(aid)
                        tracks = [_track_info(t) for t in data.get("items", [])]
                        self._player.enqueue_many(tracks)
                        app.call_from_thread(app.notify, f"Added {len(tracks)} tracks from '{atitle}'")
                    except Exception as e:
                        app.call_from_thread(app.notify, f"Failed: {e}", severity="error")
                threading.Thread(target=_load, daemon=True).start()


# ---------------------------------------------------------------------------
# Queue pane
# ---------------------------------------------------------------------------

class QueuePane(Container):
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("delete", "remove_track", "Remove"),
        Binding("ctrl+up", "move_up", "Move Up"),
        Binding("ctrl+down", "move_down", "Move Down"),
    ]

    DEFAULT_CSS = """
    QueuePane {
        height: 1fr;
    }
    QueuePane DataTable {
        height: 1fr;
        margin-top: 1;
    }
    QueuePane #queue-label {
        margin: 1;
        color: $text-muted;
    }
    """

    def __init__(self, player: Player, **kwargs):
        super().__init__(**kwargs)
        self._player = player
        self._last_version = -1
        self._last_index = -1

    def compose(self) -> ComposeResult:
        yield Label("Queue is empty", id="queue-label")
        yield DataTable(id="queue-table", cursor_type="row", zebra_stripes=True)

    def on_mount(self) -> None:
        self.query_one("#queue-table", DataTable).add_columns(
            " ", "Title", "Artist", "Album", "Duration"
        )

    def update_state(self, state: PlayerState) -> None:
        version_changed = state.queue_version != self._last_version
        index_changed = state.queue_index != self._last_index

        if version_changed:
            self._last_version = state.queue_version
            self._last_index = state.queue_index
            self._rebuild(state)
        elif index_changed:
            self._last_index = state.queue_index
            self._update_markers(state)

    def _rebuild(self, state: PlayerState) -> None:
        table = self.query_one("#queue-table", DataTable)
        cursor = table.cursor_row
        table.clear()
        for i, t in enumerate(state.queue):
            marker = "▶" if i == state.queue_index else ""
            table.add_row(marker, t.title, t.artist, t.album,
                          api.format_duration(t.duration))
        count = len(state.queue)
        self.query_one("#queue-label", Label).update(
            f"{count} track{'s' if count != 1 else ''} in queue"
            if count else "Queue is empty"
        )
        # Restore cursor if still valid
        if state.queue and cursor < len(state.queue):
            table.move_cursor(row=cursor)

    def _update_markers(self, state: PlayerState) -> None:
        table = self.query_one("#queue-table", DataTable)
        for i in range(len(state.queue)):
            marker = "▶" if i == state.queue_index else ""
            try:
                table.update_cell_at((i, 0), marker)
            except Exception:
                pass

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self._player.play_from_queue(event.cursor_row)

    def action_remove_track(self) -> None:
        idx = self.query_one("#queue-table", DataTable).cursor_row
        self._player.dequeue(idx)

    def action_move_up(self) -> None:
        table = self.query_one("#queue-table", DataTable)
        idx = table.cursor_row
        if idx > 0:
            self._player.move_in_queue(idx, idx - 1)
            table.move_cursor(row=idx - 1)

    def action_move_down(self) -> None:
        table = self.query_one("#queue-table", DataTable)
        idx = table.cursor_row
        if idx < len(self._player.state.queue) - 1:
            self._player.move_in_queue(idx, idx + 1)
            table.move_cursor(row=idx + 1)


# ---------------------------------------------------------------------------
# Recommendations pane
# ---------------------------------------------------------------------------

class RecommendationsPane(Container):
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("a", "add_to_queue", "Add to Queue"),
    ]

    DEFAULT_CSS = """
    RecommendationsPane {
        height: 1fr;
    }
    RecommendationsPane DataTable {
        height: 1fr;
        margin-top: 1;
    }
    RecommendationsPane #rec-label {
        margin: 1;
        color: $text-muted;
    }
    """

    def __init__(self, player: Player, **kwargs):
        super().__init__(**kwargs)
        self._player = player
        self._tracks: list[dict] = []

    def compose(self) -> ComposeResult:
        yield Label("Play a track to load recommendations", id="rec-label")
        yield DataTable(id="rec-table", cursor_type="row", zebra_stripes=True)

    def on_mount(self) -> None:
        table = self.query_one("#rec-table", DataTable)
        table.add_columns("Title", "Artist", "Album", "Duration")

    def load_for(self, track_id: int, track_title: str) -> None:
        self.query_one("#rec-label", Label).update(
            f"Recommendations based on: {track_title}"
        )

        def _run():
            try:
                results = api.get_recommendations(track_id)
                self.app.call_from_thread(self._populate, results)
            except Exception:
                pass

        threading.Thread(target=_run, daemon=True).start()

    def _populate(self, tracks: list[dict]) -> None:
        self._tracks = tracks
        table = self.query_one("#rec-table", DataTable)
        table.clear()
        for t in tracks:
            artist = t.get("artist", {}).get("name", "?")
            album = t.get("album", {}).get("title", "?")
            dur = api.format_duration(t.get("duration", 0))
            table.add_row(t.get("title", "?"), artist, album, dur)
        self._player.set_url_loader(lambda tid: api.get_stream_url(tid))

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        idx = event.cursor_row
        if idx < len(self._tracks):
            self.app.play_track(self._tracks[idx])  # type: ignore
            self._player.set_queue([_track_info(t) for t in self._tracks], idx)

    def action_add_to_queue(self) -> None:
        idx = self.query_one("#rec-table", DataTable).cursor_row
        if idx < len(self._tracks):
            t = self._tracks[idx]
            self._player.enqueue(_track_info(t))
            self.app.notify(f"Added to queue: {t.get('title', '?')}")  # type: ignore


# ---------------------------------------------------------------------------
# Main App
# ---------------------------------------------------------------------------

def _track_info(data: dict) -> TrackInfo:
    return TrackInfo(
        track_id=data["id"],
        title=data.get("title", "?"),
        artist=data.get("artist", {}).get("name", "?"),
        album=data.get("album", {}).get("title", "?"),
        duration=data.get("duration", 0),
    )


class HiFiApp(App):
    """HiFi TUI — browse and stream Tidal music."""

    TITLE = "HiFi TUI"
    SUB_TITLE = "Tidal Music Browser"

    CSS = """
    Screen {
        layout: vertical;
    }
    TabbedContent {
        height: 1fr;
    }
    NowPlayingBar {
        dock: bottom;
    }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("q", "quit", "Quit"),
        Binding("space", "pause", "Pause/Resume"),
        Binding("n", "next_track", "Next"),
        Binding("p", "prev_track", "Prev"),
        Binding("plus,=", "vol_up", "Vol+"),
        Binding("minus", "vol_down", "Vol-"),
        Binding("right", "seek_fwd", "→10s"),
        Binding("left", "seek_bck", "←10s"),
        Binding("f2", "mode_tracks", "Tracks"),
        Binding("f3", "mode_albums", "Albums"),
        Binding("f4", "mode_artists", "Artists"),
        Binding("s", "shuffle", "Shuffle"),
        Binding("r", "repeat", "Repeat"),
    ]

    def __init__(self):
        super().__init__()
        self._player = Player(on_state_change=self._on_player_state)

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent(id="main-tabs"):
            with TabPane("Search", id="tab-search"):
                yield SearchPane(self._player, id="search-pane")
            with TabPane("Recommendations", id="tab-rec"):
                yield RecommendationsPane(self._player, id="rec-pane")
            with TabPane("Queue", id="tab-queue"):
                yield QueuePane(self._player, id="queue-pane")
        yield NowPlayingBar(id="now-playing")
        yield Footer()

    def on_mount(self) -> None:
        self._player.set_url_loader(lambda tid: api.get_stream_url(tid))

    # ------------------------------------------------------------------
    # Player
    # ------------------------------------------------------------------

    def play_track(self, track_data: dict) -> None:
        info = _track_info(track_data)

        def _run():
            try:
                url = api.get_stream_url(info.track_id)
            except Exception as e:
                self.call_from_thread(
                    self.notify, f"Stream error [{info.track_id}]: {e}", severity="error"
                )
                return
            if url:
                self._player.play(info, url)
                self.call_from_thread(self._after_play, info)
            else:
                self.call_from_thread(
                    self.notify, f"No URL returned for: {info.title}", severity="error"
                )

        threading.Thread(target=_run, daemon=True).start()
        self.notify(f"Loading: {info.title}…")

    def _after_play(self, info: TrackInfo) -> None:
        # Trigger recommendations load
        try:
            rec = self.query_one("#rec-pane", RecommendationsPane)
            rec.load_for(info.track_id, info.title)
        except NoMatches:
            pass

    def _on_player_state(self, state: PlayerState) -> None:
        self.call_from_thread(self._update_all_bars, state)

    def _update_all_bars(self, state: PlayerState) -> None:
        for screen in self.screen_stack:
            for bar in screen.query(NowPlayingBar):
                bar.update_state(state)
            for pane in screen.query(QueuePane):
                pane.update_state(state)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_pause(self) -> None:
        self._player.pause_toggle()

    def action_next_track(self) -> None:
        self._player.next_track()

    def action_prev_track(self) -> None:
        self._player.prev_track()

    def action_vol_up(self) -> None:
        self._player.set_volume(self._player.state.volume + 10)

    def action_vol_down(self) -> None:
        self._player.set_volume(self._player.state.volume - 10)

    def action_seek_fwd(self) -> None:
        self._player.seek_relative(10)

    def action_seek_bck(self) -> None:
        self._player.seek_relative(-10)

    def action_mode_tracks(self) -> None:
        try:
            self.query_one("#search-pane", SearchPane).set_mode("tracks")
        except NoMatches:
            pass

    def action_mode_albums(self) -> None:
        try:
            self.query_one("#search-pane", SearchPane).set_mode("albums")
        except NoMatches:
            pass

    def action_mode_artists(self) -> None:
        try:
            self.query_one("#search-pane", SearchPane).set_mode("artists")
        except NoMatches:
            pass

    def action_shuffle(self) -> None:
        self._player.toggle_shuffle()
        state = self._player.state
        label = "ON" if state.shuffle else "OFF"
        self.notify(f"Shuffle {label}")

    def action_repeat(self) -> None:
        self._player.cycle_repeat()
        labels = {RepeatMode.NONE: "Off", RepeatMode.QUEUE: "Repeat Queue", RepeatMode.TRACK: "Repeat Track"}
        self.notify(f"Repeat: {labels[self._player.state.repeat]}")

    def action_quit(self) -> None:
        self._player.quit()
        self.exit()
