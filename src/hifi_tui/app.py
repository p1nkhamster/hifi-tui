"""HiFi TUI — main Textual application."""

from __future__ import annotations

import threading
from typing import ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.command import DiscoveryHit, Hit, Hits, Provider
from textual.containers import Container, Vertical
from textual.css.query import NoMatches
from textual.reactive import reactive
from textual.widgets import (
    Button,
    DataTable,
    Header,
    Input,
    Label,
    Link,
    Static,
    TabbedContent,
    TabPane,
)

from . import api, lastfm, playlists
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
        Binding("f2", "mode_tracks", "Tracks"),
        Binding("f3", "mode_albums", "Albums"),
        Binding("f4", "mode_artists", "Artists"),
        Binding("a", "add_to_queue", "Add to Queue"),
        Binding("l", "add_to_playlist", "Add to Playlist"),
        Binding("i", "show_metadata", "Info"),
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

    def action_add_to_playlist(self) -> None:
        table = self.query_one("#search-table", DataTable)
        idx = table.cursor_row
        if not self._results or idx >= len(self._results):
            return
        item = self._results[idx]
        app: HiFiApp = self.app  # type: ignore
        if self._mode == "tracks":
            app.push_screen(AddToPlaylistScreen([_track_to_storage(item)], item.get("title", "?")))
        elif self._mode == "albums":
            title = item.get("title", "Album")
            app.notify(f"Loading '{title}'…")
            def _load(album_id=item["id"], album_title=title):
                try:
                    data = api.get_album(album_id)
                    tracks = [_track_to_storage(t) for t in data.get("items", [])]
                    app.call_from_thread(app.push_screen, AddToPlaylistScreen(tracks, album_title))
                except Exception as e:
                    app.call_from_thread(app.notify, f"Failed: {e}", severity="error")
            threading.Thread(target=_load, daemon=True).start()

    def action_show_metadata(self) -> None:
        idx = self.query_one("#search-table", DataTable).cursor_row
        if not self._results or idx >= len(self._results):
            return
        if self._mode == "tracks":
            self.app.push_screen(TrackMetadataScreen(self._results[idx]["id"]))  # type: ignore

    def action_mode_tracks(self) -> None:
        self.set_mode("tracks")

    def action_mode_albums(self) -> None:
        self.set_mode("albums")

    def action_mode_artists(self) -> None:
        self.set_mode("artists")

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

from textual.screen import ModalScreen, Screen


class AlbumScreen(Screen):
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("enter", "play_selected", "Play"),
        Binding("a", "add_to_queue", "Add to Queue"),
        Binding("l", "add_to_playlist", "Add to Playlist"),
        Binding("i", "show_metadata", "Info"),
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

    def action_add_to_playlist(self) -> None:
        idx = self.query_one("#album-table", DataTable).cursor_row
        if idx < len(self._tracks):
            t = self._tracks[idx]
            self.app.push_screen(AddToPlaylistScreen([_track_to_storage(t)], t.get("title", "?")))  # type: ignore

    def action_show_metadata(self) -> None:
        idx = self.query_one("#album-table", DataTable).cursor_row
        if idx < len(self._tracks):
            self.app.push_screen(TrackMetadataScreen(self._tracks[idx]["id"]))  # type: ignore


class ArtistScreen(Screen):
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("a", "add_to_queue", "Add to Queue"),
        Binding("l", "add_to_playlist", "Add to Playlist"),
        Binding("i", "show_metadata", "Info"),
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

    def action_add_to_playlist(self) -> None:
        app: HiFiApp = self.app  # type: ignore
        try:
            active_tab = self.query_one("#artist-tabs", TabbedContent).active
        except NoMatches:
            return
        if active_tab == "tab-tracks":
            idx = self.query_one("#tracks-table", DataTable).cursor_row
            if idx < len(self._tracks):
                t = self._tracks[idx]
                app.push_screen(AddToPlaylistScreen([_track_to_storage(t)], t.get("title", "?")))
        elif active_tab in ("tab-albums", "tab-eps"):
            lst = self._albums if active_tab == "tab-albums" else self._eps_singles
            table_id = "albums-table" if active_tab == "tab-albums" else "eps-table"
            idx = self.query_one(f"#{table_id}", DataTable).cursor_row
            if idx < len(lst):
                album = lst[idx]
                title = album.get("title", "Album")
                app.notify(f"Loading '{title}'…")
                def _load(aid=album["id"], atitle=title):
                    try:
                        data = api.get_album(aid)
                        tracks = [_track_to_storage(t) for t in data.get("items", [])]
                        app.call_from_thread(app.push_screen, AddToPlaylistScreen(tracks, atitle))
                    except Exception as e:
                        app.call_from_thread(app.notify, f"Failed: {e}", severity="error")
                threading.Thread(target=_load, daemon=True).start()

    def action_show_metadata(self) -> None:
        try:
            active_tab = self.query_one("#artist-tabs", TabbedContent).active
        except NoMatches:
            return
        if active_tab == "tab-tracks":
            idx = self.query_one("#tracks-table", DataTable).cursor_row
            if idx < len(self._tracks):
                self.app.push_screen(TrackMetadataScreen(self._tracks[idx]["id"]))  # type: ignore


# ---------------------------------------------------------------------------
# Queue pane
# ---------------------------------------------------------------------------

class QueuePane(Container):
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("i", "show_metadata", "Info"),
        Binding("l", "add_to_playlist", "Add to Playlist"),
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
            " ", "Title", "Artist", "Album", "Quality", "Duration"
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
                          t.quality, api.format_duration(t.duration))
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

    def action_add_to_playlist(self) -> None:
        idx = self.query_one("#queue-table", DataTable).cursor_row
        queue = self._player.state.queue
        if idx < len(queue):
            t = queue[idx]
            track = {
                "track_id": t.track_id,
                "title": t.title,
                "artist": t.artist,
                "album": t.album,
                "duration": t.duration,
                "quality": t.quality,
            }
            self.app.push_screen(AddToPlaylistScreen([track], t.title))  # type: ignore

    def action_show_metadata(self) -> None:
        idx = self.query_one("#queue-table", DataTable).cursor_row
        queue = self._player.state.queue
        if idx < len(queue):
            self.app.push_screen(TrackMetadataScreen(queue[idx].track_id))  # type: ignore

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
        Binding("l", "add_to_playlist", "Add to Playlist"),
        Binding("i", "show_metadata", "Info"),
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
        table.add_columns("Title", "Artist", "Album", "Quality", "Duration")

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
            table.add_row(t.get("title", "?"), artist, album, _quality_label(t), dur)
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

    def action_add_to_playlist(self) -> None:
        idx = self.query_one("#rec-table", DataTable).cursor_row
        if idx < len(self._tracks):
            t = self._tracks[idx]
            self.app.push_screen(AddToPlaylistScreen([_track_to_storage(t)], t.get("title", "?")))  # type: ignore

    def action_show_metadata(self) -> None:
        idx = self.query_one("#rec-table", DataTable).cursor_row
        if idx < len(self._tracks):
            self.app.push_screen(TrackMetadataScreen(self._tracks[idx]["id"]))  # type: ignore


# ---------------------------------------------------------------------------
# Confirm modal
# ---------------------------------------------------------------------------

class ConfirmScreen(ModalScreen[bool]):
    """Simple yes/no confirmation dialog."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("y", "confirm", "Yes"),
        Binding("n", "cancel", "No"),
        Binding("escape", "cancel", "No", priority=True),
    ]

    DEFAULT_CSS = """
    ConfirmScreen {
        align: center middle;
    }
    ConfirmScreen > Vertical {
        width: 50;
        height: auto;
        background: $panel;
        border: solid $warning;
        padding: 1 2;
    }
    ConfirmScreen #confirm-msg {
        text-style: bold;
        margin-bottom: 1;
    }
    ConfirmScreen #confirm-hint {
        color: $text-muted;
    }
    """

    def __init__(self, message: str):
        super().__init__()
        self._message = message

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(self._message, id="confirm-msg")
            yield Label("y = Yes    n / Esc = No", id="confirm-hint")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


# ---------------------------------------------------------------------------
# Add-to-playlist modal
# ---------------------------------------------------------------------------

class AddToPlaylistScreen(ModalScreen):
    """Overlay for selecting (or creating) a playlist to add tracks to."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "cancel", "Cancel", priority=True),
        Binding("n", "new_playlist", "New Playlist"),
    ]

    DEFAULT_CSS = """
    AddToPlaylistScreen {
        align: center middle;
    }
    AddToPlaylistScreen > Vertical {
        width: 60;
        height: auto;
        max-height: 30;
        background: $panel;
        border: solid $accent;
        padding: 1 2;
    }
    AddToPlaylistScreen #atp-title {
        color: $accent;
        text-style: bold;
        margin-bottom: 1;
    }
    AddToPlaylistScreen #atp-hint {
        color: $text-muted;
    }
    AddToPlaylistScreen DataTable {
        height: auto;
        max-height: 15;
        margin: 1 0;
    }
    AddToPlaylistScreen #atp-input {
        margin-top: 1;
    }
    """

    def __init__(self, tracks: list[dict], label: str = ""):
        super().__init__()
        self._tracks = tracks
        self._label = label
        self._playlist_names: list[str] = []
        self._input_visible = False

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(f"Add to Playlist — {self._label}", id="atp-title")
            yield Label("Enter=add  n=new playlist  Esc=cancel", id="atp-hint")
            yield DataTable(id="atp-table", cursor_type="row", zebra_stripes=True)
            yield Input(placeholder="New playlist name…", id="atp-input")

    def on_mount(self) -> None:
        table = self.query_one("#atp-table", DataTable)
        table.add_columns("Playlist", "Tracks")
        self.query_one("#atp-input", Input).display = False
        self._refresh_table()

    def _refresh_table(self) -> None:
        pl = playlists.list_playlists()
        self._playlist_names = [p["name"] for p in pl]
        table = self.query_one("#atp-table", DataTable)
        table.clear()
        for p in pl:
            table.add_row(p["name"], str(p["track_count"]))
        if pl:
            table.focus()
        else:
            self.action_new_playlist()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        idx = event.cursor_row
        if idx < len(self._playlist_names):
            self._do_add(self._playlist_names[idx])

    def _do_add(self, name: str) -> None:
        added = playlists.add_tracks(name, self._tracks)
        self.app.notify(f"Added {added} track(s) to '{name}'")
        self.dismiss()

    def action_new_playlist(self) -> None:
        inp = self.query_one("#atp-input", Input)
        inp.display = True
        inp.focus()
        self._input_visible = True

    def on_input_submitted(self, event: Input.Submitted) -> None:
        name = event.value.strip()
        if name:
            playlists.create_playlist(name)
            self._do_add(name)
        else:
            event.input.display = False
            self._input_visible = False
            self.query_one("#atp-table", DataTable).focus()

    def action_cancel(self) -> None:
        if self._input_visible:
            inp = self.query_one("#atp-input", Input)
            inp.display = False
            inp.value = ""
            self._input_visible = False
            self.query_one("#atp-table", DataTable).focus()
        else:
            self.dismiss()


# ---------------------------------------------------------------------------
# Track metadata modal
# ---------------------------------------------------------------------------

class TrackMetadataScreen(ModalScreen):
    """Fetches and displays full track metadata."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "dismiss", "Close"),
    ]

    DEFAULT_CSS = """
    TrackMetadataScreen {
        align: center middle;
    }
    TrackMetadataScreen > Vertical {
        width: 64;
        height: auto;
        background: $panel;
        border: solid $accent;
        padding: 1 2;
    }
    TrackMetadataScreen #meta-title {
        color: $accent;
        text-style: bold;
        margin-bottom: 1;
    }
    TrackMetadataScreen #meta-body {
        color: $text;
    }
    TrackMetadataScreen #meta-hint {
        color: $text-muted;
        margin-top: 1;
    }
    """

    def __init__(self, track_id: int):
        super().__init__()
        self._track_id = track_id

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("Loading…", id="meta-title")
            yield Label("", id="meta-body")
            yield Label("Esc = close", id="meta-hint")

    def on_mount(self) -> None:
        def _load():
            try:
                d = api.get_track_info(self._track_id)
                self.app.call_from_thread(self._populate, d)
            except Exception as e:
                self.app.call_from_thread(
                    self.query_one("#meta-body", Label).update,
                    f"Error: {e}"
                )
        threading.Thread(target=_load, daemon=True).start()

    def _populate(self, d: dict) -> None:
        title = d.get("title", "?")
        version = d.get("version")
        if version:
            title += f" ({version})"
        artist = d.get("artist", {}).get("name", "?")
        album = d.get("album", {}).get("title", "?")
        track_num = d.get("trackNumber", "?")
        vol_num = d.get("volumeNumber")
        track_str = f"{track_num}" if vol_num in (None, 1) else f"{track_num} (disc {vol_num})"
        duration = api.format_duration(d.get("duration", 0))
        quality = _quality_label(d)
        bpm = d.get("bpm")
        key = d.get("key")
        scale = d.get("keyScale", "")
        explicit = "Yes" if d.get("explicit") else "No"
        popularity = d.get("popularity", "?")
        isrc = d.get("isrc", "?")
        copyright_ = d.get("copyright", "?")
        modes = ", ".join(d.get("audioModes", [])) or "?"

        def row(label: str, value) -> str:
            return f"  {label:<14} {value}"

        lines = [
            row("Title", title),
            row("Artist", artist),
            row("Album", album),
            row("Track", track_str),
            row("Duration", duration),
            row("Quality", quality),
            row("Audio mode", modes),
        ]
        if bpm:
            lines.append(row("BPM", bpm))
        if key:
            lines.append(row("Key", f"{key} {scale.lower()}".strip()))
        lines += [
            row("Explicit", explicit),
            row("Popularity", popularity),
            row("ISRC", isrc),
            row("Copyright", copyright_),
        ]

        self.query_one("#meta-title", Label).update(f"Track info — {d.get('id', '')}")
        self.query_one("#meta-body", Label).update("\n".join(lines))


# ---------------------------------------------------------------------------
# Playlist screen (pushed)
# ---------------------------------------------------------------------------

class PlaylistScreen(Screen):
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "dismiss", "Back"),
        Binding("a", "add_to_queue", "Add to Queue"),
        Binding("l", "add_to_playlist", "Add to Playlist"),
        Binding("i", "show_metadata", "Info"),
        Binding("delete", "remove_track", "Remove"),
    ]

    DEFAULT_CSS = """
    PlaylistScreen {
        background: $surface;
    }
    PlaylistScreen #pl-header {
        text-style: bold;
        color: $accent;
        margin: 1;
    }
    PlaylistScreen DataTable {
        height: 1fr;
    }
    """

    def __init__(self, name: str, player: Player):
        super().__init__()
        self._name = name
        self._player = player
        self._tracks: list[dict] = []

    def compose(self) -> ComposeResult:
        yield Label(f"Playlist: {self._name}", id="pl-header")
        yield DataTable(id="pl-table", cursor_type="row", zebra_stripes=True)
        yield NowPlayingBar(id="now-playing")

    def on_mount(self) -> None:
        self.query_one(NowPlayingBar).update_state(self._player.state)
        self.query_one("#pl-table", DataTable).add_columns(
            "Title", "Artist", "Album", "Quality", "Duration"
        )
        self._reload()

    def _reload(self) -> None:
        self._tracks = playlists.load_playlist(self._name)
        table = self.query_one("#pl-table", DataTable)
        table.clear()
        for t in self._tracks:
            table.add_row(
                t.get("title", "?"), t.get("artist", "?"), t.get("album", "?"),
                t.get("quality", "?"), api.format_duration(t.get("duration", 0))
            )
        self.query_one("#pl-header", Label).update(
            f"Playlist: {self._name}  ({len(self._tracks)} tracks)"
        )

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        idx = event.cursor_row
        if idx < len(self._tracks):
            info = _storage_to_track_info(self._tracks[idx])
            queue = [_storage_to_track_info(t) for t in self._tracks]
            self._player.set_queue(queue, idx)
            self.app.play_track_info(info)  # type: ignore

    def action_add_to_queue(self) -> None:
        idx = self.query_one("#pl-table", DataTable).cursor_row
        if idx < len(self._tracks):
            t = self._tracks[idx]
            self._player.enqueue(_storage_to_track_info(t))
            self.app.notify(f"Added to queue: {t.get('title', '?')}")  # type: ignore

    def action_remove_track(self) -> None:
        idx = self.query_one("#pl-table", DataTable).cursor_row
        if idx < len(self._tracks):
            title = self._tracks[idx].get("title", "?")
            playlists.remove_track(self._name, idx)
            self._reload()
            self.app.notify(f"Removed '{title}' from playlist")  # type: ignore

    def action_add_to_playlist(self) -> None:
        idx = self.query_one("#pl-table", DataTable).cursor_row
        if idx < len(self._tracks):
            t = self._tracks[idx]
            self.app.push_screen(AddToPlaylistScreen([t], t.get("title", "?")))  # type: ignore

    def action_show_metadata(self) -> None:
        idx = self.query_one("#pl-table", DataTable).cursor_row
        if idx < len(self._tracks):
            self.app.push_screen(TrackMetadataScreen(self._tracks[idx]["track_id"]))  # type: ignore


# ---------------------------------------------------------------------------
# Playlists pane
# ---------------------------------------------------------------------------

class PlaylistsPane(Container):
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("n", "new_playlist", "New Playlist"),
        Binding("ctrl+r", "rename_playlist", "Rename"),
        Binding("delete", "delete_playlist", "Delete"),
    ]

    DEFAULT_CSS = """
    PlaylistsPane {
        height: 1fr;
    }
    PlaylistsPane DataTable {
        height: 1fr;
        margin-top: 1;
    }
    PlaylistsPane #plp-hint {
        margin: 1;
        color: $text-muted;
    }
    PlaylistsPane #plp-input {
        margin: 0 1;
    }
    """

    def __init__(self, player: Player, **kwargs):
        super().__init__(**kwargs)
        self._player = player
        self._playlist_names: list[str] = []

    def compose(self) -> ComposeResult:
        yield Label("n=new  Enter=open  ^r=rename  Del=delete", id="plp-hint")
        yield Input(placeholder="New playlist name…", id="plp-input")
        yield Input(placeholder="Rename playlist to…", id="plp-rename-input")
        yield DataTable(id="plp-table", cursor_type="row", zebra_stripes=True)

    def on_mount(self) -> None:
        self.query_one("#plp-table", DataTable).add_columns("Playlist", "Tracks")
        self.query_one("#plp-input", Input).display = False
        self.query_one("#plp-rename-input", Input).display = False
        self._refresh()

    def on_show(self) -> None:
        self._refresh()

    def _refresh(self) -> None:
        pl = playlists.list_playlists()
        self._playlist_names = [p["name"] for p in pl]
        table = self.query_one("#plp-table", DataTable)
        table.clear()
        for p in pl:
            table.add_row(p["name"], str(p["track_count"]))
        count = len(pl)
        self.query_one("#plp-hint", Label).update(
            f"{count} playlist(s)  —  n=new  Enter=open  ^r=rename  Del=delete"
            if count else "No playlists yet  —  press n to create one"
        )

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        idx = event.cursor_row
        if idx < len(self._playlist_names):
            self.app.push_screen(PlaylistScreen(self._playlist_names[idx], self._player), lambda _: self._refresh())  # type: ignore

    def action_new_playlist(self) -> None:
        inp = self.query_one("#plp-input", Input)
        inp.display = True
        inp.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        name = event.value.strip()
        if event.input.id == "plp-rename-input":
            old_name = getattr(self, "_renaming", None)
            if name and old_name:
                playlists.rename_playlist(old_name, name)
                self.app.notify(f"Renamed '{old_name}' to '{name}'")  # type: ignore
            self._renaming = None
        else:
            if name:
                playlists.create_playlist(name)
                self.app.notify(f"Created playlist '{name}'")  # type: ignore
        event.input.value = ""
        event.input.display = False
        self._refresh()
        self.query_one("#plp-table", DataTable).focus()

    def action_rename_playlist(self) -> None:
        idx = self.query_one("#plp-table", DataTable).cursor_row
        if idx < len(self._playlist_names):
            self._renaming = self._playlist_names[idx]
            inp = self.query_one("#plp-rename-input", Input)
            inp.value = self._renaming
            inp.display = True
            inp.focus()

    def action_delete_playlist(self) -> None:
        idx = self.query_one("#plp-table", DataTable).cursor_row
        if idx < len(self._playlist_names):
            name = self._playlist_names[idx]
            def _on_confirm(confirmed: bool) -> None:
                if confirmed:
                    playlists.delete_playlist(name)
                    self._refresh()
                    self.app.notify(f"Deleted playlist '{name}'")  # type: ignore
            self.app.push_screen(ConfirmScreen(f"Delete playlist '{name}'?"), _on_confirm)  # type: ignore


# ---------------------------------------------------------------------------
# Settings pane (Last.fm)
# ---------------------------------------------------------------------------

class SettingsPane(Container):
    DEFAULT_CSS = """
    SettingsPane {
        height: 1fr;
        padding: 1 2;
    }
    SettingsPane #s-heading {
        text-style: bold;
        color: $accent;
        margin-bottom: 1;
    }
    SettingsPane #s-status {
        margin-bottom: 1;
    }
    SettingsPane #s-auth-url {
        height: auto;
        margin-bottom: 1;
    }
    SettingsPane #s-auth-url Label {
        color: $text-muted;
    }
    SettingsPane #s-auth-url Link {
        color: $warning;
    }
    SettingsPane .s-label {
        color: $text-muted;
        margin-top: 1;
    }
    SettingsPane #s-api-hint {
        color: $text-muted;
        margin-bottom: 1;
    }
    SettingsPane Button {
        margin-top: 1;
        width: 30;
    }
    """

    def compose(self) -> ComposeResult:
        yield Label("Last.fm", id="s-heading")
        yield Label("", id="s-status")
        yield Container(
            Label("Get your API key and secret at "),
            Link("last.fm/api/account/create", url="https://www.last.fm/api/account/create"),
            id="s-api-hint",
        )
        yield Label("API Key:", classes="s-label")
        yield Input(placeholder="Paste your Last.fm API key", id="s-api-key")
        yield Label("API Secret:", classes="s-label")
        yield Input(placeholder="Paste your Last.fm API secret", password=True, id="s-api-secret")
        yield Button("Save Credentials", id="s-btn-save", variant="primary")
        yield Container(id="s-auth-url")
        yield Button("Get Auth URL", id="s-btn-auth", variant="success")
        yield Button("Complete Auth (after browser approval)", id="s-btn-complete", variant="success")
        yield Button("Disconnect", id="s-btn-disconnect", variant="error")

    def on_mount(self) -> None:
        self._pending_token: str | None = None
        self._prefill()
        self._refresh()

    def on_show(self) -> None:
        self._prefill()
        self._refresh()

    def _prefill(self) -> None:
        lfm: lastfm.LastFM = self.app._lastfm  # type: ignore
        self.query_one("#s-api-key", Input).value = lfm.api_key
        self.query_one("#s-api-secret", Input).value = lfm.api_secret

    def _refresh(self) -> None:
        lfm: lastfm.LastFM = self.app._lastfm  # type: ignore
        if lfm.is_authenticated:
            status = f"Status: Connected as {lfm.username}"
        elif lfm.is_configured:
            status = "Status: Credentials saved — not connected yet"
        else:
            status = "Status: Not configured"
        self.query_one("#s-status", Label).update(status)

        self.query_one("#s-api-hint").display = not lfm.is_authenticated
        self.query_one("#s-btn-auth").display = lfm.is_configured and not lfm.is_authenticated
        self.query_one("#s-btn-complete").display = bool(self._pending_token)
        self.query_one("#s-btn-disconnect").display = lfm.is_authenticated

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "s-api-key":
            self.query_one("#s-api-secret", Input).focus()
        elif event.input.id == "s-api-secret":
            self._save_credentials()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "s-btn-save":
            self._save_credentials()
        elif bid == "s-btn-auth":
            self._start_auth()
        elif bid == "s-btn-complete":
            self._complete_auth()
        elif bid == "s-btn-disconnect":
            self._disconnect()

    def _save_credentials(self) -> None:
        api_key = self.query_one("#s-api-key", Input).value.strip()
        api_secret = self.query_one("#s-api-secret", Input).value.strip()
        if api_key and api_secret:
            lfm: lastfm.LastFM = self.app._lastfm  # type: ignore
            lfm.set_credentials(api_key, api_secret)
            self.app.notify("Last.fm credentials saved")  # type: ignore
            self._refresh()
        else:
            self.app.notify("Fill in both API key and secret", severity="warning")  # type: ignore

    def _start_auth(self) -> None:
        lfm: lastfm.LastFM = self.app._lastfm  # type: ignore
        self.app.notify("Getting auth token…")  # type: ignore
        def _get():
            try:
                token = lfm.get_auth_token()
                url = lfm.get_auth_url(token)
                self.app.call_from_thread(self._on_token, token, url)
            except Exception as e:
                self.app.call_from_thread(self.app.notify, f"Last.fm: {e}", severity="error")
        threading.Thread(target=_get, daemon=True).start()

    def _on_token(self, token: str, url: str) -> None:
        self._pending_token = token
        container = self.query_one("#s-auth-url", Container)
        container.remove_children()
        container.mount(Label("Open in your browser:"))
        container.mount(Link(url, url=url))
        self._refresh()

    def _complete_auth(self) -> None:
        if not self._pending_token:
            return
        token = self._pending_token
        lfm: lastfm.LastFM = self.app._lastfm  # type: ignore
        def _complete():
            try:
                username = lfm.complete_auth(token)
                self.app.call_from_thread(self._on_auth_complete, username)
            except Exception as e:
                self.app.call_from_thread(self.app.notify, f"Last.fm auth failed: {e}", severity="error")
        threading.Thread(target=_complete, daemon=True).start()

    def _on_auth_complete(self, username: str) -> None:
        self._pending_token = None
        self.query_one("#s-auth-url", Container).remove_children()
        self._refresh()
        self.app.notify(f"Last.fm: connected as {username}!")  # type: ignore

    def _disconnect(self) -> None:
        lfm: lastfm.LastFM = self.app._lastfm  # type: ignore
        lfm.disconnect()
        self._refresh()
        self.app.notify("Disconnected from Last.fm")  # type: ignore


# ---------------------------------------------------------------------------
# Command palette
# ---------------------------------------------------------------------------

_HIFI_BINDINGS: list[tuple[str, str, str | None]] = [
    # Global playback
    ("Space",   "Pause / Resume playback",                  "pause"),
    ("N",       "Next track",                               "next_track"),
    ("P",       "Previous track",                           "prev_track"),
    ("+ / =",   "Volume up",                               "vol_up"),
    ("-",       "Volume down",                             "vol_down"),
    ("→",       "Seek forward 10 seconds",                  "seek_fwd"),
    ("←",       "Seek back 10 seconds",                     "seek_bck"),
    ("S",       "Toggle shuffle",                           "shuffle"),
    ("R",       "Toggle repeat",                            "repeat"),
    ("Ctrl+I",  "Show info for currently playing track",    "show_playing_metadata"),
    ("Ctrl+L",  "Add currently playing track to playlist",  "add_playing_to_playlist"),
    ("Q",       "Quit",                                     "quit"),
    # Context-specific (all tabs)
    ("I",       "Show info for selected track",             None),
    ("A",       "Add selected track / album to queue",      None),
    ("L",       "Add selected track to playlist",           None),
    # Search tab
    ("F2",      "[Search] Switch to Tracks search mode",    None),
    ("F3",      "[Search] Switch to Albums search mode",    None),
    ("F4",      "[Search] Switch to Artists search mode",   None),
    # Queue tab
    ("Ctrl+↑",  "[Queue] Move track up",                    None),
    ("Ctrl+↓",  "[Queue] Move track down",                  None),
    ("Delete",  "[Queue] Remove selected track",            None),
    # Playlists tab
    ("N",       "[Playlists] Create new playlist",          None),
    ("Ctrl+R",  "[Playlists] Rename selected playlist",     None),
    ("Delete",  "[Playlists] Delete selected playlist",     None),
]


class HifiCommandProvider(Provider):
    """Lists all hifi-tui keybindings in the command palette."""

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        for key, desc, action in _HIFI_BINDINGS:
            text = f"{key}  —  {desc}"
            score = matcher.match(text)
            if score > 0:
                if action:
                    async def cmd(a: str = action) -> None:
                        await self.app.run_action(a)
                    yield Hit(score, matcher.highlight(text), cmd, text=text, help=desc)
                else:
                    yield Hit(score, matcher.highlight(text), lambda: None, text=text, help=desc)

    async def discover(self) -> Hits:
        for key, desc, action in _HIFI_BINDINGS:
            text = f"{key}  —  {desc}"
            if action:
                async def cmd(a: str = action) -> None:
                    await self.app.run_action(a)
                yield DiscoveryHit(text, cmd, text=text, help=desc)
            else:
                yield DiscoveryHit(text, lambda: None, text=text, help=desc)


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
        quality=_quality_label(data),
    )


def _track_to_storage(data: dict) -> dict:
    """Convert API track dict to playlist storage format."""
    return {
        "track_id": data["id"],
        "title": data.get("title", "?"),
        "artist": data.get("artist", {}).get("name", "?"),
        "album": data.get("album", {}).get("title", "?"),
        "duration": data.get("duration", 0),
        "quality": _quality_label(data),
    }


def _storage_to_track_info(d: dict) -> TrackInfo:
    """Convert playlist storage dict to TrackInfo."""
    return TrackInfo(
        track_id=d["track_id"],
        title=d.get("title", "?"),
        artist=d.get("artist", "?"),
        album=d.get("album", "?"),
        duration=d.get("duration", 0),
        quality=d.get("quality", ""),
    )


class HiFiApp(App):
    """HiFi TUI — browse and stream Tidal music."""

    TITLE = "HiFi TUI"
    SUB_TITLE = "Tidal Music Browser  ·  ^P: keybinds"

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
        Binding("s", "shuffle", "Shuffle"),
        Binding("r", "repeat", "Repeat"),
        Binding("ctrl+l", "add_playing_to_playlist", "Add Playing to Playlist"),
        Binding("ctrl+i", "show_playing_metadata", "Playing Info"),
    ]

    COMMANDS = {HifiCommandProvider}

    def __init__(self):
        super().__init__()
        self._player = Player(on_state_change=self._on_player_state)
        self._lastfm = lastfm.LastFM()
        self._scrobbler = lastfm.Scrobbler(self._lastfm)
        self._last_scrobble_track_id: int | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent(id="main-tabs"):
            with TabPane("Search", id="tab-search"):
                yield SearchPane(self._player, id="search-pane")
            with TabPane("Recommendations", id="tab-rec"):
                yield RecommendationsPane(self._player, id="rec-pane")
            with TabPane("Queue", id="tab-queue"):
                yield QueuePane(self._player, id="queue-pane")
            with TabPane("Playlists", id="tab-playlists"):
                yield PlaylistsPane(self._player, id="playlists-pane")
            with TabPane("Settings", id="tab-settings"):
                yield SettingsPane(id="settings-pane")
        yield NowPlayingBar(id="now-playing")

    def on_mount(self) -> None:
        self._player.set_url_loader(lambda tid: api.get_stream_url(tid))

    # ------------------------------------------------------------------
    # Player
    # ------------------------------------------------------------------

    def play_track_info(self, info: TrackInfo) -> None:
        """Play a TrackInfo directly (used by PlaylistScreen)."""
        def _run():
            try:
                url = api.get_stream_url(info.track_id)
            except Exception as e:
                self.call_from_thread(self.notify, f"Stream error: {e}", severity="error")
                return
            if url:
                self._player.play(info, url)
                self.call_from_thread(self._after_play, info)
        threading.Thread(target=_run, daemon=True).start()
        self.notify(f"Loading: {info.title}…")

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
        # Scrobbling
        if state.track is None:
            self._last_scrobble_track_id = None
            self._scrobbler.reset()
        elif state.playing:
            if state.track.track_id != self._last_scrobble_track_id:
                self._last_scrobble_track_id = state.track.track_id
                self._scrobbler.track_started(state.track)
            self._scrobbler.update(
                state.position,
                on_error=lambda e: self.call_from_thread(
                    self.notify, f"Last.fm: {e}", severity="warning"
                ),
            )

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

    def action_shuffle(self) -> None:
        self._player.toggle_shuffle()
        state = self._player.state
        label = "ON" if state.shuffle else "OFF"
        self.notify(f"Shuffle {label}")

    def action_repeat(self) -> None:
        self._player.cycle_repeat()
        labels = {RepeatMode.NONE: "Off", RepeatMode.QUEUE: "Repeat Queue", RepeatMode.TRACK: "Repeat Track"}
        self.notify(f"Repeat: {labels[self._player.state.repeat]}")

    def action_add_playing_to_playlist(self) -> None:
        state = self._player.state
        if state.track is None:
            self.notify("Nothing is playing", severity="warning")
            return
        t = state.track
        track = {
            "track_id": t.track_id,
            "title": t.title,
            "artist": t.artist,
            "album": t.album,
            "duration": t.duration,
            "quality": t.quality,
        }
        self.push_screen(AddToPlaylistScreen([track], t.title))

    def action_show_playing_metadata(self) -> None:
        state = self._player.state
        if state.track is None:
            self.notify("Nothing is playing", severity="warning")
            return
        self.push_screen(TrackMetadataScreen(state.track.track_id))

    def action_quit(self) -> None:
        self._player.quit()
        self.exit()
