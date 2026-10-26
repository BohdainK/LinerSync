"""A stateful Spotify Web API simulator on httpx.MockTransport.

Serves ``api.spotify.com`` (the 2026 shapes recorded in tests/fixtures/spotify),
``accounts.spotify.com`` (the token endpoint) and image hosts. Faults can be
injected per request pattern.
"""

import base64
import binascii
import hashlib
import io
import json as _json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx
from PIL import Image

from linersync.adapters.connectors.spotify.moves import Move, apply_moves
from linersync.domain.tracks import Track

API_HOST = "api.spotify.com"
ACCOUNTS_HOST = "accounts.spotify.com"
ART_PREFIX = "https://i.scdn.co/image/art-"
_LOCAL = "spotify:local:"
_MAX_IMAGE_BODY = 256 * 1024


def html_escape(text: str) -> str:
    """Spotify escapes these four characters in names and descriptions."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _jpeg(seed: str, size: int = 64) -> bytes:
    digest = hashlib.sha256(seed.encode()).digest()
    img = Image.new("RGB", (size, size), (digest[0], digest[1], digest[2]))
    for x in range(size // 2):
        img.putpixel((x, x), (digest[3], digest[4], digest[5]))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


@dataclass
class _Playlist:
    pid: str
    name: str
    description: str
    owner: str
    collaborative: bool
    uris: list[str]
    image_url: str | None
    public: bool = False
    version: int = 1

    @property
    def snapshot_id(self) -> str:
        return f"snap-{self.pid}-{self.version}"


@dataclass
class _Fault:
    method: str
    pattern: re.Pattern[str]
    times: int
    apply_first: bool
    status: int | None  # None: raise httpx.ReadTimeout
    json: Any = None
    headers: Mapping[str, str] = field(default_factory=dict)


class SpotifySim:
    def __init__(self, *, me_id: str = "me", catalog: Sequence[Track]) -> None:
        self.me_id = me_id
        self.catalog = {t.track_id: t for t in catalog}
        self.playlists: dict[str, _Playlist] = {}
        self.images: dict[str, bytes] = {}
        self.requests: list[httpx.Request] = []
        self.access_token = "sim-access-0"
        self._valid_tokens = {self.access_token}
        self._grant_dead = False
        self._faults: list[_Fault] = []
        self._truncated: set[str] = set()
        self._change_during_read: set[str] = set()
        self._count = 0
        self.transport = httpx.MockTransport(self.handle)

    # -- arrangement -----------------------------------------------------

    def add_playlist(
        self,
        name: str,
        uris: Sequence[str],
        *,
        owner: str = "me",
        collaborative: bool = False,
        description: str = "",
        image_url: str | None = None,
    ) -> str:
        self._count += 1
        pid = f"simpl{self._count:04d}"
        owner_id = self.me_id if owner == "me" else owner
        self.playlists[pid] = _Playlist(
            pid, name, description, owner_id, collaborative, list(uris), image_url
        )
        return pid

    def uris(self, playlist_id: str) -> list[str]:
        return list(self.playlists[playlist_id].uris)

    # -- faults ----------------------------------------------------------

    def fail_next(
        self,
        method: str,
        path_regex: str,
        status: int,
        *,
        times: int = 1,
        apply_first: bool = False,
        json: Any = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self._faults.append(
            _Fault(
                method,
                re.compile(path_regex),
                times,
                apply_first,
                status,
                json,
                dict(headers or {}),
            )
        )

    def timeout_next(self, method: str, path_regex: str, *, apply_first: bool) -> None:
        self._faults.append(
            _Fault(method, re.compile(path_regex), 1, apply_first, None)
        )

    def truncate(self, playlist_id: str) -> None:
        """Report a total one higher than the items served (until reset)."""
        self._truncated.add(playlist_id)

    def change_during_read(self, playlist_id: str) -> None:
        """The snapshot_id changes once, right after the next first items page."""
        self._change_during_read.add(playlist_id)

    def expire_login(self) -> None:
        self._grant_dead = True
        self._valid_tokens.clear()

    # -- transport -------------------------------------------------------

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if request.url.host == API_HOST and path.startswith("/v1"):
            path = path[3:]
        fault = self._take_fault(request.method, path)
        if fault is None:
            return self._route(request, path)
        if fault.apply_first:
            self._route(request, path)
        if fault.status is None:
            raise httpx.ReadTimeout("simulated timeout", request=request)
        body = fault.json
        if body is None:
            body = {"error": {"status": fault.status, "message": "Simulated failure"}}
        return httpx.Response(fault.status, json=body, headers=dict(fault.headers))

    def _take_fault(self, method: str, path: str) -> _Fault | None:
        for fault in self._faults:
            if fault.method == method and fault.pattern.fullmatch(path):
                fault.times -= 1
                if fault.times <= 0:
                    self._faults.remove(fault)
                return fault
        return None

    def _route(self, request: httpx.Request, path: str) -> httpx.Response:
        host = request.url.host
        if host == ACCOUNTS_HOST:
            return self._token(request)
        if host != API_HOST:
            return self._image(request)
        auth = request.headers.get("authorization", "")
        if auth.removeprefix("Bearer ") not in self._valid_tokens:
            return _error(401, "The access token expired")
        method = request.method
        routes: list[tuple[str, str, Callable[..., httpx.Response]]] = [
            ("GET", r"/me", self._me),
            ("GET", r"/me/playlists", self._my_playlists),
            ("POST", r"/me/playlists", self._create),
            ("GET", r"/search", self._search),
            ("GET", r"/playlists/([^/]+)", self._get_playlist),
            ("PUT", r"/playlists/([^/]+)", self._change_details),
            ("GET", r"/playlists/([^/]+)/items", self._get_items),
            ("POST", r"/playlists/([^/]+)/items", self._add_items),
            ("DELETE", r"/playlists/([^/]+)/items", self._remove_items),
            ("PUT", r"/playlists/([^/]+)/items", self._move_items),
            ("PUT", r"/playlists/([^/]+)/images", self._upload_image),
        ]
        for m, pattern, fn in routes:
            match = re.fullmatch(pattern, path)
            if m == method and match:
                return fn(request, *match.groups())
        return _error(404, "Service not found")

    # -- accounts / images -------------------------------------------------

    def _token(self, request: httpx.Request) -> httpx.Response:
        if request.method != "POST" or request.url.path != "/api/token":
            return _error(404, "Not found")
        if self._grant_dead:
            return httpx.Response(400, json={"error": "invalid_grant"})
        self._count += 1
        token = f"sim-access-{self._count}"
        self._valid_tokens.add(token)
        self.access_token = token
        return httpx.Response(
            200,
            json={"access_token": token, "token_type": "Bearer", "expires_in": 3600},
        )

    def _image(self, request: httpx.Request) -> httpx.Response:
        if "authorization" in request.headers:
            return _error(400, "credentials sent to an image host")
        url = str(request.url)
        data = self.images.get(url)
        if data is None and url.startswith(ART_PREFIX):
            data = _jpeg(url)
        if request.method != "GET" or data is None:
            return httpx.Response(404)
        return httpx.Response(200, content=data, headers={"Content-Type": "image/jpeg"})

    # -- API -------------------------------------------------------------

    def _me(self, request: httpx.Request) -> httpx.Response:
        me = self.me_id
        return httpx.Response(
            200,
            json={
                "id": me,
                "display_name": "Me Tester",
                "uri": f"spotify:user:{me}",
                "href": f"https://api.spotify.com/v1/users/{me}",
                "external_urls": {"spotify": f"https://open.spotify.com/user/{me}"},
                "images": [],
                "type": "user",
            },
        )

    def _readable(self, p: _Playlist) -> bool:
        return p.owner == self.me_id or p.collaborative

    def _total(self, p: _Playlist) -> int:
        return len(p.uris) + (1 if p.pid in self._truncated else 0)

    def _playlist_obj(self, p: _Playlist, *, full: bool) -> dict[str, Any]:
        obj: dict[str, Any] = {
            "id": p.pid,
            "name": html_escape(p.name),
            "description": html_escape(p.description),
            "collaborative": p.collaborative,
            "public": p.public,
            "snapshot_id": p.snapshot_id,
            "owner": {"id": p.owner, "display_name": f"User {p.owner}"},
            "images": (
                [{"url": p.image_url, "height": 640, "width": 640}]
                if p.image_url
                else []
            ),
            "uri": f"spotify:playlist:{p.pid}",
            "type": "playlist",
        }
        href = f"https://api.spotify.com/v1/playlists/{p.pid}/items"
        if self._readable(p):
            items: dict[str, Any] = {"href": href, "total": self._total(p)}
            if full:
                items.update(limit=100, next=None, offset=0, previous=None, items=[])
            obj["items"] = items
        return obj

    def _my_playlists(self, request: httpx.Request) -> httpx.Response:
        offset, limit = _paging(request, default=20, maximum=50)
        if limit is None:
            return _error(400, "Invalid limit")
        mine = list(self.playlists.values())
        chunk = mine[offset : offset + limit]
        base = "https://api.spotify.com/v1/me/playlists"
        return httpx.Response(
            200,
            json=_page(
                base,
                offset,
                limit,
                len(mine),
                [self._playlist_obj(p, full=False) for p in chunk],
            ),
        )

    def _create(self, request: httpx.Request) -> httpx.Response:
        body = _body(request)
        if not isinstance(body, dict) or not isinstance(body.get("name"), str):
            return _error(400, "Missing name")
        pid = self.add_playlist(
            body["name"], [], description=body.get("description", "")
        )
        p = self.playlists[pid]
        p.public = bool(body.get("public", True))
        return httpx.Response(201, json=self._playlist_obj(p, full=True))

    def _get_playlist(self, request: httpx.Request, pid: str) -> httpx.Response:
        p = self.playlists.get(pid)
        if p is None:
            return _error(404, "Resource not found")
        return httpx.Response(200, json=self._playlist_obj(p, full=True))

    def _writable(self, pid: str) -> tuple[_Playlist | None, httpx.Response | None]:
        p = self.playlists.get(pid)
        if p is None:
            return None, _error(404, "Resource not found")
        if not self._readable(p):
            return None, _error(403, "Forbidden")
        return p, None

    def _changed(self, p: _Playlist, status: int = 200) -> httpx.Response:
        p.version += 1
        return httpx.Response(status, json={"snapshot_id": p.snapshot_id})

    def _change_details(self, request: httpx.Request, pid: str) -> httpx.Response:
        p, error = self._writable(pid)
        if p is None:
            return error or _error(500, "unreachable")
        if p.owner != self.me_id:
            return _error(403, "Only the owner can change details")
        body = _body(request)
        if not isinstance(body, dict):
            return _error(400, "Invalid body")
        if isinstance(body.get("name"), str):
            p.name = body["name"]
        if isinstance(body.get("description"), str):
            p.description = body["description"]
        if "public" in body:
            p.public = bool(body["public"])
        p.version += 1
        return httpx.Response(200)

    def _get_items(self, request: httpx.Request, pid: str) -> httpx.Response:
        p = self.playlists.get(pid)
        if p is None:
            return _error(404, "Resource not found")
        if not self._readable(p):
            return _error(403, "Forbidden")
        offset, limit = _paging(request, default=20, maximum=50)
        if limit is None:
            return _error(400, "Invalid limit")
        types = request.url.params.get("additional_types", "")
        base = f"https://api.spotify.com/v1/playlists/{pid}/items"
        extra = {"additional_types": types} if types else {}
        items = [self._item_entry(uri) for uri in p.uris[offset : offset + limit]]
        page = _page(base, offset, limit, self._total(p), items, extra)
        if offset + limit >= len(p.uris):
            page["next"] = None  # a truncated playlist stops short of its total
        if offset == 0 and pid in self._change_during_read:
            self._change_during_read.discard(pid)
            p.version += 1
        return httpx.Response(200, json=page)

    def _item_entry(self, uri: str) -> dict[str, Any]:
        local = uri.startswith(_LOCAL)
        return {
            "added_at": "2026-03-01T10:00:00Z",
            "added_by": {"id": self.me_id},
            "is_local": local,
            "item": self.track_obj(uri),
        }

    def track_obj(self, uri: str) -> dict[str, Any]:
        t = self.catalog[uri]
        kind, _, ident = uri.removeprefix("spotify:").partition(":")
        if kind == "episode":
            return {
                "type": "episode",
                "id": ident,
                "uri": uri,
                "name": t.title,
                "duration_ms": t.duration_ms,
                "show": {"name": t.artists[0] if t.artists else ""},
                "images": [{"url": f"{ART_PREFIX}{ident}", "height": 64, "width": 64}],
                "external_urls": {
                    "spotify": f"https://open.spotify.com/episode/{ident}"
                },
            }
        local = kind == "local"
        images = (
            []
            if local
            else [{"url": f"{ART_PREFIX}{ident}", "height": 64, "width": 64}]
        )
        return {
            "type": "track",
            "id": None if local else ident,
            "uri": uri,
            "name": t.title,
            "duration_ms": t.duration_ms,
            "artists": [{"name": a} for a in t.artists],
            "album": {"name": t.album or "", "images": images},
            "external_ids": {"isrc": t.isrc} if t.isrc and not local else {},
            "external_urls": (
                {} if local else {"spotify": f"https://open.spotify.com/track/{ident}"}
            ),
        }

    def _add_items(self, request: httpx.Request, pid: str) -> httpx.Response:
        p, error = self._writable(pid)
        if p is None:
            return error or _error(500, "unreachable")
        body = _body(request)
        uris = body.get("uris") if isinstance(body, dict) else None
        if not isinstance(uris, list) or not 1 <= len(uris) <= 100:
            return _error(400, "Send 1 to 100 uris")
        for uri in uris:
            if not isinstance(uri, str) or uri.startswith(_LOCAL):
                return _error(400, "Local tracks can't be added")
            if uri not in self.catalog:
                return _error(400, f"Invalid uri {uri}")
        position = body.get("position") if isinstance(body, dict) else None
        if position is None:
            p.uris.extend(uris)
        elif isinstance(position, int) and 0 <= position <= len(p.uris):
            p.uris[position:position] = uris
        else:
            return _error(400, "Index out of bounds")
        return self._changed(p, 201)

    def _remove_items(self, request: httpx.Request, pid: str) -> httpx.Response:
        p, error = self._writable(pid)
        if p is None:
            return error or _error(500, "unreachable")
        body = _body(request)
        items = body.get("items") if isinstance(body, dict) else None
        if not isinstance(items, list) or not 1 <= len(items) <= 100:
            return _error(400, "Send 1 to 100 items")
        doomed = {i.get("uri") for i in items if isinstance(i, dict)}
        p.uris = [u for u in p.uris if u not in doomed]
        return self._changed(p)

    def _move_items(self, request: httpx.Request, pid: str) -> httpx.Response:
        p, error = self._writable(pid)
        if p is None:
            return error or _error(500, "unreachable")
        body = _body(request)
        if not isinstance(body, dict):
            return _error(400, "Invalid body")
        start, before = body.get("range_start"), body.get("insert_before")
        if body.get("range_length", 1) != 1:
            return _error(400, "The simulator only moves one item at a time")
        n = len(p.uris)
        if not (isinstance(start, int) and isinstance(before, int)):
            return _error(400, "range_start and insert_before are required")
        if not (0 <= start < n and 0 <= before <= n):
            return _error(400, "Index out of bounds")
        p.uris = apply_moves(p.uris, [Move(start, before)])
        return self._changed(p)

    def _upload_image(self, request: httpx.Request, pid: str) -> httpx.Response:
        p, error = self._writable(pid)
        if p is None:
            return error or _error(500, "unreachable")
        if request.headers.get("content-type") != "image/jpeg":
            return _error(400, "Content-Type must be image/jpeg")
        if len(request.content) > _MAX_IMAGE_BODY:
            return _error(413, "Image too large")
        try:
            data = base64.b64decode(request.content, validate=True)
            with Image.open(io.BytesIO(data)) as img:
                if img.format != "JPEG":
                    return _error(400, "Not a JPEG")
        except (binascii.Error, OSError):
            return _error(400, "Not a base64 JPEG")
        p.version += 1
        url = f"https://i.scdn.co/image/cover-{pid}-{p.version}"
        self.images[url] = data
        p.image_url = url
        return httpx.Response(202)

    def _search(self, request: httpx.Request) -> httpx.Response:
        params = request.url.params
        q = params.get("q", "")
        try:
            limit = int(params.get("limit", "10"))
        except ValueError:
            return _error(400, "Invalid limit")
        if params.get("type") != "track" or not 1 <= limit <= 10 or not q:
            return _error(400, "Bad search request")
        tracks = [t for uri, t in self.catalog.items() if not uri.startswith(_LOCAL)]
        if q.startswith("isrc:"):
            isrc = q.removeprefix("isrc:")
            hits = [t for t in tracks if t.isrc == isrc]
        else:
            m = re.search(r'track:"([^"]*)"', q)
            needle = (m.group(1) if m else q).casefold()
            hits = [t for t in tracks if needle in t.title.casefold()]
        hits = [t for t in hits if not t.track_id.startswith("spotify:episode:")]
        items = [self.track_obj(t.track_id) for t in hits[:limit]]
        page = _page("https://api.spotify.com/v1/search", 0, limit, len(hits), items)
        page["href"] = "https://api.spotify.com/v1/search"
        page["next"] = None
        return httpx.Response(200, json={"tracks": page})


def _error(status: int, message: str) -> httpx.Response:
    return httpx.Response(
        status, json={"error": {"status": status, "message": message}}
    )


def _body(request: httpx.Request) -> Any:
    try:
        return _json.loads(request.content or b"null")
    except ValueError:
        return None


def _paging(
    request: httpx.Request, *, default: int, maximum: int
) -> tuple[int, int | None]:
    params = request.url.params
    try:
        offset = int(params.get("offset", "0"))
        limit = int(params.get("limit", str(default)))
    except ValueError:
        return 0, None
    if offset < 0 or not 1 <= limit <= maximum:
        return 0, None
    return offset, limit


def _page(
    base: str,
    offset: int,
    limit: int,
    total: int,
    items: list[dict[str, Any]],
    extra: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    def url(at: int) -> str:
        return str(
            httpx.URL(base, params={"offset": at, "limit": limit, **(extra or {})})
        )

    return {
        "href": url(offset),
        "limit": limit,
        "next": url(offset + limit) if offset + limit < total else None,
        "offset": offset,
        "previous": url(max(0, offset - limit)) if offset > 0 else None,
        "total": total,
        "items": items,
    }
