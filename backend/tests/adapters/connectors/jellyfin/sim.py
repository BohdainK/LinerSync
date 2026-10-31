"""A stateful Jellyfin REST simulator on httpx.MockTransport.

Serves one server under a path prefix (``BASE_URL``), shaped like the
fixtures in tests/fixtures/jellyfin. Faults can be injected per request
pattern.
"""

import hashlib
import io
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx
from PIL import Image

from linersync.domain.tracks import Track

BASE_URL = "http://jellyfin.test/jf"
_PREFIX = "/jf"
_SERVER_ID = "5f1c0a6f2e8b4c4b9a1d3e7f6a2b8c9d"
VIDEO_ID = "f" * 32


@dataclass
class _Playlist:
    pid: str
    name: str
    overview: str
    ids: list[str] = field(default_factory=list)
    entry_ids: list[str] = field(default_factory=list)


@dataclass
class _Fault:
    method: str
    pattern: re.Pattern[str]
    times: int
    status: int


class JellyfinSim:
    def __init__(
        self,
        *,
        catalog: Sequence[Track],
        api_key: str = "k" * 32,
        users: Sequence[tuple[str, str]] = (("Sam", "u" * 32),),
    ) -> None:
        self.catalog = {t.track_id: t for t in catalog}
        self.api_key = api_key
        self.users = list(users)
        self.playlists: dict[str, _Playlist] = {}
        self.requests: list[httpx.Request] = []
        self._revoked = False
        self._faults: list[_Fault] = []
        self._truncated: set[str] = set()
        self._change_during_read: set[str] = set()
        self._swap_during_read: set[str] = set()
        self._count = 0
        self._entries = 0
        self.transport = httpx.MockTransport(self.handle)

    def credentials(self, username: str = "Sam") -> dict[str, str]:
        return {"server_url": BASE_URL, "api_key": self.api_key, "username": username}

    # -- arrangement -----------------------------------------------------

    def add_playlist(
        self, name: str, track_ids: Sequence[str], *, overview: str = ""
    ) -> str:
        self._count += 1
        pid = f"{self._count:032x}"
        self.playlists[pid] = _Playlist(pid, name, overview)
        for tid in track_ids:
            self._append(pid, tid)
        return pid

    def add_non_audio(self, playlist_id: str) -> None:
        self._append(playlist_id, VIDEO_ID)

    def _append(self, playlist_id: str, track_id: str) -> None:
        self._entries += 1
        p = self.playlists[playlist_id]
        p.ids.append(track_id)
        p.entry_ids.append(f"{self._entries:032x}")

    # -- faults ----------------------------------------------------------

    def fail_next(
        self, method: str, path_regex: str, status: int, *, times: int = 1
    ) -> None:
        self._faults.append(_Fault(method, re.compile(path_regex), times, status))

    def truncate(self, playlist_id: str) -> None:
        """Report a total one higher than the items served."""
        self._truncated.add(playlist_id)

    def change_during_read(self, playlist_id: str) -> None:
        """A song is appended right after the next first items page."""
        self._change_during_read.add(playlist_id)

    def swap_during_read(self, playlist_id: str) -> None:
        """Right after the next first items page, the first song is removed and
        another appended: the length stays the same."""
        self._swap_during_read.add(playlist_id)

    def revoke_key(self) -> None:
        self._revoked = True

    # -- transport -------------------------------------------------------

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if not path.startswith(_PREFIX + "/"):
            return httpx.Response(404)
        path = path[len(_PREFIX) :]
        for fault in self._faults:
            if fault.method == request.method and fault.pattern.fullmatch(path):
                fault.times -= 1
                if fault.times <= 0:
                    self._faults.remove(fault)
                return httpx.Response(fault.status, text="Simulated failure")
        return self._route(request, path)

    def _route(self, request: httpx.Request, path: str) -> httpx.Response:
        image = re.fullmatch(r"/Items/([0-9a-f]{32})/Images/Primary", path)
        if image and request.method == "GET":
            return httpx.Response(200, content=_jpeg(image.group(1)))
        if not self._authorised(request):
            return httpx.Response(401, text="Unauthorized")
        if request.method != "GET":
            return httpx.Response(405)
        if path == "/Users":
            return httpx.Response(200, json=[self._user(n, i) for n, i in self.users])
        if path == "/Items":
            return self._items(request)
        items = re.fullmatch(r"/Playlists/([^/]+)/Items", path)
        if items:
            return self._playlist_items(request, items.group(1))
        return httpx.Response(404)

    def _authorised(self, request: httpx.Request) -> bool:
        expected = f'MediaBrowser Token="{self.api_key}"'
        return not self._revoked and request.headers.get("Authorization") == expected

    def _user_ok(self, request: httpx.Request) -> bool:
        return request.url.params.get("userId") in {i for _, i in self.users}

    def _items(self, request: httpx.Request) -> httpx.Response:
        params = request.url.params
        if not self._user_ok(request):
            return httpx.Response(400, text="userId is required")
        kind = params.get("IncludeItemTypes")
        if kind == "Playlist":
            wanted = params.get("Ids")
            found = [
                self._playlist_obj(p)
                for p in self.playlists.values()
                if wanted is None or p.pid in wanted.split(",")
            ]
            return httpx.Response(200, json=_result(found, 0))
        if kind == "Audio":
            term = (params.get("SearchTerm") or "").lower()
            limit = int(params.get("Limit", "100"))
            hits = [
                self._song(tid)
                for tid, t in self.catalog.items()
                if term in t.title.lower()
            ]
            return httpx.Response(200, json=_result(hits[:limit], 0, len(hits)))
        return httpx.Response(400, text="unsupported IncludeItemTypes")

    def _playlist_items(self, request: httpx.Request, pid: str) -> httpx.Response:
        if not self._user_ok(request):
            return httpx.Response(400, text="userId is required")
        p = self.playlists.get(pid)
        if p is None:
            return httpx.Response(404, text="Playlist not found")
        params = request.url.params
        start = int(params.get("StartIndex", "0"))
        limit = int(params.get("Limit", str(len(p.ids))))
        total = len(p.ids) + (1 if pid in self._truncated else 0)
        window = range(start, min(start + limit, len(p.ids)))
        page = [
            {**self._song(p.ids[i]), "PlaylistItemId": p.entry_ids[i]} for i in window
        ]
        if start == 0 and pid in self._change_during_read:
            self._change_during_read.discard(pid)
            self._append(pid, p.ids[0])
        if start == 0 and pid in self._swap_during_read:
            self._swap_during_read.discard(pid)
            first = p.ids[0]
            del p.ids[0], p.entry_ids[0]
            self._append(pid, first)
        return httpx.Response(200, json=_result(page, start, total))

    def _playlist_obj(self, p: _Playlist) -> dict[str, Any]:
        return {
            "Name": p.name,
            "ServerId": _SERVER_ID,
            "Id": p.pid,
            "Overview": p.overview,
            "ChildCount": len(p.ids),
            "Type": "Playlist",
            "MediaType": "Audio",
            "ImageTags": {},
        }

    def _song(self, tid: str) -> dict[str, Any]:
        if tid == VIDEO_ID:
            return {"Name": "Clip", "Id": VIDEO_ID, "Type": "Video", "ImageTags": {}}
        t = self.catalog[tid]
        return {
            "Name": t.title,
            "ServerId": _SERVER_ID,
            "Id": t.track_id,
            "RunTimeTicks": (t.duration_ms or 0) * 10_000,
            "Type": "Audio",
            "MediaType": "Audio",
            "Artists": list(t.artists),
            "AlbumArtist": t.artists[0] if t.artists else "",
            "Album": t.album or "",
            "ImageTags": {"Primary": hashlib.md5(tid.encode()).hexdigest()},
            "ProviderIds": {},
        }

    @staticmethod
    def _user(name: str, user_id: str) -> dict[str, Any]:
        return {
            "Name": name,
            "ServerId": _SERVER_ID,
            "Id": user_id,
            "HasPassword": True,
            "Policy": {"IsAdministrator": False, "IsDisabled": False},
        }


def _result(
    items: list[dict[str, Any]], start: int, total: int | None = None
) -> dict[str, Any]:
    return {
        "Items": items,
        "TotalRecordCount": len(items) if total is None else total,
        "StartIndex": start,
    }


def _jpeg(seed: str, size: int = 64) -> bytes:
    digest = hashlib.sha256(seed.encode()).digest()
    img = Image.new("RGB", (size, size), (digest[0], digest[1], digest[2]))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()
