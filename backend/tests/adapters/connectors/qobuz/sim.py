"""A stateful simulator of the Qobuz web player API on httpx.MockTransport.

Serves ``www.qobuz.com/api.json/0.2`` in the shapes of tests/fixtures/qobuz
(hand-shaped from the community clients; no recorded traffic yet) and
``static.qobuz.com`` artwork. It checks the app ID and token headers, that
writes are form POSTs, and honours ``no_duplicate`` the way Qobuz documents
it. Faults can be injected per endpoint.
"""

import hashlib
import io
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx
from PIL import Image

from linersync.domain.tracks import Track

API_HOST = "www.qobuz.com"
API_PREFIX = "/api.json/0.2/"
ART_HOST = "static.qobuz.com"
MAX_LIMIT = 500
_EPOCH = 1730000000


def _jpeg(seed: str, size: int = 64) -> bytes:
    digest = hashlib.sha256(seed.encode()).digest()
    img = Image.new("RGB", (size, size), (digest[0], digest[1], digest[2]))
    for x in range(size // 2):
        img.putpixel((x, x), (digest[3], digest[4], digest[5]))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _error(status: int, message: str) -> httpx.Response:
    return httpx.Response(
        status, json={"status": "error", "code": status, "message": message}
    )


@dataclass
class _Item:
    track_id: str
    playlist_track_id: int
    added: int


@dataclass
class _Playlist:
    pid: int
    name: str
    description: str
    owner_id: int
    owner_name: str
    items: list[_Item] = field(default_factory=list)
    updated: int = _EPOCH
    is_public: bool = False


@dataclass
class _Fault:
    pattern: re.Pattern[str]
    times: int
    apply_first: bool
    status: int | None  # None: raise httpx.ReadTimeout
    skip: int = 0


class QobuzSim:
    def __init__(
        self,
        *,
        catalog: Sequence[Track],
        app_id: str = "123456789",
        token: str = "qobuz-token",
        user_id: int = 1111111,
        login: str = "sam",
    ) -> None:
        self.catalog = {t.track_id: t for t in catalog}
        self.app_id = app_id
        self.token = token
        self.user_id = user_id
        self.login = login
        self.playlists: dict[str, _Playlist] = {}
        self.requests: list[httpx.Request] = []
        self.page_cap = MAX_LIMIT  # Qobuz may serve fewer items than asked
        self.include_user = True  # getUserPlaylists names the user
        self.include_list_total = True  # getUserPlaylists says how many there are
        self._token_valid = True
        self._faults: list[_Fault] = []
        self._truncated: set[str] = set()
        self._change_during_read: set[str] = set()
        self._shift_during_read: set[str] = set()
        self._next_pid = 8500000
        self._next_ptid = 3775400000
        self.transport = httpx.MockTransport(self.handle)

    # -- arrangement -----------------------------------------------------

    def credentials(self) -> dict[str, str]:
        return {"app_id": self.app_id, "user_auth_token": self.token}

    def _item(self, track_id: str) -> _Item:
        self._next_ptid += 1
        return _Item(track_id, self._next_ptid, _EPOCH)

    def add_playlist(
        self,
        name: str,
        track_ids: Sequence[str],
        *,
        owner_id: int | None = None,
        owner_name: str | None = None,
    ) -> str:
        self._next_pid += 1
        pid = str(self._next_pid)
        self.playlists[pid] = _Playlist(
            self._next_pid,
            name,
            "",
            self.user_id if owner_id is None else owner_id,
            owner_name or self.login,
            [self._item(t) for t in track_ids],
        )
        return pid

    def track_ids(self, pid: str) -> list[str]:
        return [i.track_id for i in self.playlists[pid].items]

    def playlist_track_ids(self, pid: str) -> list[int]:
        return [i.playlist_track_id for i in self.playlists[pid].items]

    def truncate(self, pid: str) -> None:
        """Report one song more than the playlist serves."""
        self._truncated.add(pid)

    def change_during_read(self, pid: str) -> None:
        """A song is inserted at the top right after the next first page."""
        self._change_during_read.add(pid)

    def shift_during_read(self, pid: str) -> None:
        """Right after the next first page, the top song is removed and one is
        appended: the length stays, and updated_at doesn't move (it isn't
        known to)."""
        self._shift_during_read.add(pid)

    def expire_token(self) -> None:
        self._token_valid = False

    def fail_next(
        self,
        endpoint_regex: str,
        status: int,
        *,
        times: int = 1,
        apply_first: bool = False,
        skip: int = 0,
    ) -> None:
        self._faults.append(
            _Fault(re.compile(endpoint_regex), times, apply_first, status, skip)
        )

    def timeout_next(
        self, endpoint_regex: str, *, apply_first: bool, times: int = 1
    ) -> None:
        self._faults.append(
            _Fault(re.compile(endpoint_regex), times, apply_first, None)
        )

    # -- transport -------------------------------------------------------

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == ART_HOST:
            return self._image(request)
        path = request.url.path
        if request.url.host != API_HOST or not path.startswith(API_PREFIX):
            return _error(404, "Not found")
        endpoint = path.removeprefix(API_PREFIX)
        fault = self._take_fault(endpoint)
        if fault is None:
            return self._route(request, endpoint)
        if fault.apply_first:
            self._route(request, endpoint)
        if fault.status is None:
            raise httpx.ReadTimeout("simulated timeout", request=request)
        return _error(fault.status, "Simulated failure")

    def _take_fault(self, endpoint: str) -> _Fault | None:
        for fault in self._faults:
            if fault.pattern.fullmatch(endpoint):
                if fault.skip > 0:
                    fault.skip -= 1
                    return None
                fault.times -= 1
                if fault.times <= 0:
                    self._faults.remove(fault)
                return fault
        return None

    def _route(self, request: httpx.Request, endpoint: str) -> httpx.Response:
        if request.headers.get("x-app-id") != self.app_id:
            return _error(400, "Invalid or missing app_id parameter")
        if not self._token_valid or (
            request.headers.get("x-user-auth-token") != self.token
        ):
            return _error(401, "User authentication is required.")
        reads: dict[str, Callable[[httpx.QueryParams], httpx.Response]] = {
            "playlist/getUserPlaylists": self._user_playlists,
            "playlist/get": self._get_playlist,
            "track/search": self._search,
        }
        writes: dict[str, Callable[[dict[str, str]], httpx.Response]] = {
            "playlist/create": self._create,
            "playlist/update": self._update,
            "playlist/addTracks": self._add_tracks,
            "playlist/deleteTracks": self._delete_tracks,
        }
        if endpoint in reads:
            if request.method != "GET":
                return _error(405, "Method not allowed")
            return reads[endpoint](request.url.params)
        if endpoint in writes:
            ctype = request.headers.get("content-type", "")
            if request.method != "POST" or "x-www-form-urlencoded" not in ctype:
                return _error(400, "Expected a form POST")
            form = dict(httpx.QueryParams(request.content.decode()))
            return writes[endpoint](form)
        return _error(404, "Not found")

    def _image(self, request: httpx.Request) -> httpx.Response:
        if "x-user-auth-token" in request.headers:
            return _error(400, "credentials sent to an image host")
        return httpx.Response(
            200, content=_jpeg(str(request.url)), headers={"Content-Type": "image/jpeg"}
        )

    # -- shapes ----------------------------------------------------------

    def _track_obj(self, track_id: str) -> dict[str, Any]:
        t = self.catalog[track_id]
        main, *others = t.artists or ("Unknown",)
        performers = " - ".join(
            [f"{main}, MainArtist", *(f"{a}, FeaturedArtist" for a in others)]
        )
        obj: dict[str, Any] = {
            "id": int(track_id),
            "title": t.title,
            "version": None,
            "isrc": t.isrc,
            "track_number": 1,
            "media_number": 1,
            "parental_warning": False,
            "streamable": True,
            "performer": {"id": 11111, "name": main},
            "performers": performers,
            "album": {
                "id": f"alb{track_id}",
                "title": t.album or "Album",
                "image": {
                    "small": f"https://{ART_HOST}/images/covers/{track_id}_230.jpg",
                    "thumbnail": f"https://{ART_HOST}/images/covers/{track_id}_50.jpg",
                    "large": f"https://{ART_HOST}/images/covers/{track_id}_600.jpg",
                },
                "artist": {"id": 11111, "name": main},
            },
        }
        if t.duration_ms is not None:
            obj["duration"] = t.duration_ms // 1000
        return obj

    def _playlist_obj(self, p: _Playlist) -> dict[str, Any]:
        count = len(p.items) + (1 if str(p.pid) in self._truncated else 0)
        return {
            "id": p.pid,
            "name": p.name,
            "description": p.description,
            "tracks_count": count,
            "users_count": 0,
            "duration": 0,
            "created_at": _EPOCH,
            "updated_at": p.updated,
            "is_public": p.is_public,
            "is_collaborative": False,
            "owner": {"id": p.owner_id, "name": p.owner_name},
            "images300": [f"https://{ART_HOST}/images/covers/pl{p.pid}_300.jpg"],
        }

    @staticmethod
    def _window(params: httpx.QueryParams, cap: int) -> tuple[int, int]:
        offset = int(params.get("offset", 0))
        limit = min(int(params.get("limit", 50)), cap)
        return offset, limit

    # -- reads -----------------------------------------------------------

    def _user_playlists(self, params: httpx.QueryParams) -> httpx.Response:
        offset, limit = self._window(params, self.page_cap)
        mine = list(self.playlists.values())
        body: dict[str, Any] = {
            "playlists": {
                "offset": offset,
                "limit": limit,
                "total": len(mine),
                "items": [self._playlist_obj(p) for p in mine[offset : offset + limit]],
            }
        }
        if not self.include_list_total:
            del body["playlists"]["total"]
        if self.include_user:
            body["user"] = {"id": self.user_id, "login": self.login}
        return httpx.Response(200, json=body)

    def _get_playlist(self, params: httpx.QueryParams) -> httpx.Response:
        pid = params.get("playlist_id", "")
        p = self.playlists.get(pid)
        if p is None:
            return _error(404, "No result matching given argument")
        body = self._playlist_obj(p)
        if params.get("extra") == "tracks":
            offset, limit = self._window(params, self.page_cap)
            window = p.items[offset : offset + limit]
            body["tracks"] = {
                "offset": offset,
                "limit": limit,
                "total": body["tracks_count"],
                "items": [
                    {
                        **self._track_obj(i.track_id),
                        "playlist_track_id": i.playlist_track_id,
                        "position": offset + n + 1,
                        "created_at": i.added,
                    }
                    for n, i in enumerate(window)
                ],
            }
            if offset == 0 and pid in self._shift_during_read:
                self._shift_during_read.discard(pid)
                p.items.append(self._item(p.items.pop(0).track_id))
            if offset == 0 and pid in self._change_during_read:
                self._change_during_read.discard(pid)
                p.items.insert(0, self._item(p.items[-1].track_id))
                p.updated += 1
        return httpx.Response(200, json=body)

    def _search(self, params: httpx.QueryParams) -> httpx.Response:
        query = params.get("query", "").strip().lower()
        limit = min(int(params.get("limit", 50)), MAX_LIMIT)
        words = query.split()
        hits = [
            t
            for t in self.catalog.values()
            if (t.isrc and t.isrc.lower() == query)
            or (
                words
                and all(w in f"{' '.join(t.artists)} {t.title}".lower() for w in words)
            )
        ]
        return httpx.Response(
            200,
            json={
                "query": query,
                "tracks": {
                    "limit": limit,
                    "offset": 0,
                    "total": len(hits),
                    "items": [self._track_obj(t.track_id) for t in hits[:limit]],
                },
            },
        )

    # -- writes ----------------------------------------------------------

    def _owned(self, form: dict[str, str]) -> _Playlist | httpx.Response:
        p = self.playlists.get(form.get("playlist_id", ""))
        if p is None:
            return _error(404, "No result matching given argument")
        if p.owner_id != self.user_id:
            return _error(403, "Insufficient permissions")
        return p

    def _create(self, form: dict[str, str]) -> httpx.Response:
        if not form.get("name"):
            return _error(400, "name is required")
        pid = self.add_playlist(form["name"], [])
        p = self.playlists[pid]
        p.description = form.get("description", "")
        p.is_public = form.get("is_public") == "true"
        return httpx.Response(200, json=self._playlist_obj(p))

    def _update(self, form: dict[str, str]) -> httpx.Response:
        p = self._owned(form)
        if isinstance(p, httpx.Response):
            return p
        if "name" in form:
            p.name = form["name"]
        if "description" in form:
            p.description = form["description"]
        p.updated += 1
        return httpx.Response(200, json=self._playlist_obj(p))

    def _add_tracks(self, form: dict[str, str]) -> httpx.Response:
        p = self._owned(form)
        if isinstance(p, httpx.Response):
            return p
        ids = [i for i in form.get("track_ids", "").split(",") if i]
        if not ids or any(i not in self.catalog for i in ids):
            return _error(400, "Invalid track_ids")
        skip_repeats = form.get("no_duplicate", "true") != "false"
        for tid in ids:
            if skip_repeats and tid in {i.track_id for i in p.items}:
                continue
            p.items.append(self._item(tid))
        p.updated += 1
        return httpx.Response(200, json=self._playlist_obj(p))

    def _delete_tracks(self, form: dict[str, str]) -> httpx.Response:
        p = self._owned(form)
        if isinstance(p, httpx.Response):
            return p
        gone = {i for i in form.get("playlist_track_ids", "").split(",") if i}
        if not gone:
            return _error(400, "playlist_track_ids is required")
        p.items = [i for i in p.items if str(i.playlist_track_id) not in gone]
        p.updated += 1
        return httpx.Response(200, json=self._playlist_obj(p))
