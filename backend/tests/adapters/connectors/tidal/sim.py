"""A stateful TIDAL API v2 simulator on httpx.MockTransport.

Serves ``openapi.tidal.com`` (the JSON:API shapes in tests/fixtures/tidal),
``auth.tidal.com`` (the token endpoint) and ``resources.tidal.com`` (artwork).
It checks what the real API is strict about: the JSON:API media type, the
batch limits, the include paths the connector uses, and ``Idempotency-Key``
replay. Faults can be injected per request pattern.
"""

import hashlib
import io
import json as _json
import re
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from PIL import Image

from linersync.domain.tracks import Track

API_HOST = "openapi.tidal.com"
AUTH_HOST = "auth.tidal.com"
ART_HOST = "resources.tidal.com"
JSON_API = "application/vnd.api+json"
PAGE = 20
_EPOCH = datetime(2026, 10, 30, 12, tzinfo=UTC)
_ITEM_INCLUDES = {"items", "items.artists", "items.albums", "items.albums.coverArt"}
_TRACK_INCLUDES = {"artists", "albums", "albums.coverArt"}
_SEARCH_INCLUDES = {
    "tracks",
    "tracks.artists",
    "tracks.albums",
    "tracks.albums.coverArt",
}


def _jpeg(seed: str, size: int = 64) -> bytes:
    digest = hashlib.sha256(seed.encode()).digest()
    img = Image.new("RGB", (size, size), (digest[0], digest[1], digest[2]))
    for x in range(size // 2):
        img.putpixel((x, x), (digest[3], digest[4], digest[5]))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


@dataclass
class _Item:
    type: str  # "tracks" | "videos"
    id: str
    item_id: str
    added_at: datetime


@dataclass
class _Playlist:
    pid: str
    name: str
    description: str
    access: str
    items: list[_Item]
    version: int = 0

    @property
    def last_modified(self) -> str:
        return (
            (_EPOCH + timedelta(seconds=self.version))
            .isoformat()
            .replace("+00:00", "Z")
        )


@dataclass
class _Fault:
    method: str
    pattern: re.Pattern[str]
    times: int
    apply_first: bool
    status: int | None  # None: raise httpx.ReadTimeout
    headers: Mapping[str, str] = field(default_factory=dict)
    skip: int = 0  # matching requests to let through first


class TidalSim:
    def __init__(
        self,
        *,
        catalog: Sequence[Track],
        access_token: str = "tidal-access",
        country: str = "NL",
        username: str = "sam",
    ) -> None:
        self.catalog = {t.track_id: t for t in catalog}
        self.country = country
        self.username = username
        self.user_id = "123456789"
        self.playlists: dict[str, _Playlist] = {}
        self.videos: dict[str, str] = {}  # id -> title
        self.requests: list[httpx.Request] = []
        self.access_token = access_token
        self._valid_tokens = {access_token}
        self._grant_dead = False
        self._faults: list[_Fault] = []
        self._truncated: set[str] = set()
        self._change_during_read: set[str] = set()
        self._unavailable: set[str] = set()  # item ids
        self._replies: dict[str, tuple[str, httpx.Response]] = {}
        self._count = 0
        # True: links.next carries only page[cursor], as the spec's example does.
        self.bare_next_links = False
        self.transport = httpx.MockTransport(self.handle)

    # -- arrangement -----------------------------------------------------

    def _next_item_id(self) -> str:
        self._count += 1
        return f"item-{self._count:05d}"

    def add_playlist(self, name: str, track_ids: Sequence[str]) -> str:
        pid = str(uuid.UUID(int=len(self.playlists) + 1))
        items = [self._new_item(t) for t in track_ids]
        self.playlists[pid] = _Playlist(pid, name, "", "UNLISTED", items)
        return pid

    def _new_item(self, track_id: str) -> _Item:
        type_, id_ = (
            ("videos", track_id[6:])
            if track_id.startswith("video:")
            else ("tracks", track_id)
        )
        return _Item(type_, id_, self._next_item_id(), _EPOCH)

    def track_ids(self, pid: str) -> list[str]:
        return [
            f"video:{i.id}" if i.type == "videos" else i.id
            for i in self.playlists[pid].items
        ]

    def item_ids(self, pid: str) -> list[str]:
        return [i.item_id for i in self.playlists[pid].items]

    def add_video(self, pid: str) -> str:
        vid = f"{9000 + len(self.videos)}"
        self.videos[vid] = f"Video {vid}"
        self.playlists[pid].items.append(
            _Item("videos", vid, self._next_item_id(), _EPOCH)
        )
        self.playlists[pid].version += 1
        return f"video:{vid}"

    def make_unavailable(self, pid: str, position: int) -> None:
        self._unavailable.add(self.playlists[pid].items[position].item_id)

    def credentials(self) -> dict[str, str]:
        """What a signed-in connection stores."""
        return {
            "client_id": "client",
            "access_token": self.access_token,
            "refresh_token": "tidal-refresh",
            "expires_at": (_EPOCH + timedelta(days=1)).isoformat(),
            "country_code": self.country,
        }

    # -- faults ----------------------------------------------------------

    def fail_next(
        self,
        method: str,
        path_regex: str,
        status: int,
        *,
        times: int = 1,
        apply_first: bool = False,
        headers: Mapping[str, str] | None = None,
        skip: int = 0,
    ) -> None:
        self._faults.append(
            _Fault(
                method,
                re.compile(path_regex),
                times,
                apply_first,
                status,
                dict(headers or {}),
                skip,
            )
        )

    def timeout_next(
        self, method: str, path_regex: str, *, apply_first: bool, times: int = 1
    ) -> None:
        self._faults.append(
            _Fault(method, re.compile(path_regex), times, apply_first, None)
        )

    def truncate(self, pid: str) -> None:
        """Report numberOfItems one higher than the items served."""
        self._truncated.add(pid)

    def change_during_read(self, pid: str) -> None:
        """The playlist changes once, right after the next first items page."""
        self._change_during_read.add(pid)

    def expire_login(self) -> None:
        self._grant_dead = True
        self._valid_tokens.clear()

    # -- transport -------------------------------------------------------

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if request.url.host == API_HOST and path.startswith("/v2"):
            path = path[3:]
        fault = self._take_fault(request.method, path)
        if fault is None:
            return self._dispatch(request, path)
        if fault.apply_first:
            self._dispatch(request, path)
        if fault.status is None:
            raise httpx.ReadTimeout("simulated timeout", request=request)
        return _error(fault.status, "Simulated failure", dict(fault.headers))

    def _take_fault(self, method: str, path: str) -> _Fault | None:
        for fault in self._faults:
            if fault.method == method and fault.pattern.fullmatch(path):
                if fault.skip > 0:
                    fault.skip -= 1
                    return None
                fault.times -= 1
                if fault.times <= 0:
                    self._faults.remove(fault)
                return fault
        return None

    def _dispatch(self, request: httpx.Request, path: str) -> httpx.Response:
        key = request.headers.get("idempotency-key")
        if key is None or request.method == "GET" or request.url.host != API_HOST:
            return self._route(request, path)
        fingerprint = f"{request.method} {request.url} {request.content!r}"
        seen = self._replies.get(key)
        if seen is not None:
            if seen[0] != fingerprint:
                return _error(422, "Idempotency-Key reused with another request")
            return seen[1]
        response = self._route(request, path)
        if response.is_success:
            response.read()
            self._replies[key] = (fingerprint, response)
        return response

    def _route(self, request: httpx.Request, path: str) -> httpx.Response:
        host = request.url.host
        if host == AUTH_HOST:
            return self._token(request)
        if host == ART_HOST:
            return self._image(request)
        auth = request.headers.get("authorization", "")
        if auth.removeprefix("Bearer ") not in self._valid_tokens:
            return _error(401, "The access token expired")
        if JSON_API not in request.headers.get("accept", ""):
            return _error(406, "Not acceptable")
        if request.content and request.headers.get("content-type") != JSON_API:
            return _error(415, "Unsupported media type")
        routes: list[tuple[str, str, Callable[..., httpx.Response]]] = [
            ("GET", r"/users/me", self._me),
            ("GET", r"/playlists", self._list_playlists),
            ("POST", r"/playlists", self._create),
            ("GET", r"/tracks", self._tracks_by_isrc),
            ("GET", r"/searchResults/(.+)/relationships/tracks", self._search),
            ("GET", r"/playlists/([^/]+)", self._get_playlist),
            ("PATCH", r"/playlists/([^/]+)", self._update_playlist),
            ("GET", r"/playlists/([^/]+)/relationships/items", self._get_items),
            ("POST", r"/playlists/([^/]+)/relationships/items", self._add_items),
            ("DELETE", r"/playlists/([^/]+)/relationships/items", self._remove_items),
            ("PATCH", r"/playlists/([^/]+)/relationships/items", self._move_items),
        ]
        for method, pattern, fn in routes:
            match = re.fullmatch(pattern, path)
            if method == request.method and match:
                return fn(request, *match.groups())
        return _error(404, "Not found")

    # -- auth / images -----------------------------------------------------

    def _token(self, request: httpx.Request) -> httpx.Response:
        if request.method != "POST" or request.url.path != "/v1/oauth2/token":
            return _error(404, "Not found")
        if self._grant_dead:
            return httpx.Response(400, json={"error": "invalid_grant"})
        self._count += 1
        token = f"tidal-access-{self._count}"
        self._valid_tokens.add(token)
        self.access_token = token
        body: dict[str, Any] = {
            "access_token": token,
            "token_type": "Bearer",
            "expires_in": 86400,
        }
        if b"grant_type=authorization_code" in request.content:
            body["refresh_token"] = f"tidal-refresh-{self._count}"
        return httpx.Response(200, json=body)

    def _image(self, request: httpx.Request) -> httpx.Response:
        if "authorization" in request.headers:
            return _error(400, "credentials sent to an image host")
        if request.method != "GET":
            return httpx.Response(404)
        return httpx.Response(
            200, content=_jpeg(str(request.url)), headers={"Content-Type": "image/jpeg"}
        )

    # -- resources -------------------------------------------------------

    def _country_ok(self, request: httpx.Request) -> bool:
        return bool(request.url.params.get("countryCode") == self.country)

    def _includes(self, request: httpx.Request, allowed: set[str]) -> set[str] | None:
        raw = request.url.params.get("include", "")
        wanted = {p for p in raw.split(",") if p}
        return wanted if wanted <= allowed else None

    def _track_resource(self, track_id: str) -> dict[str, Any]:
        t = self.catalog[track_id]
        attrs: dict[str, Any] = {
            "title": t.title,
            "isrc": t.isrc,
            "explicit": False,
            "key": "UNKNOWN",
            "keyScale": "UNKNOWN",
            "mediaTags": [],
            "popularity": 0.5,
        }
        if t.duration_ms is not None:
            attrs["duration"] = f"PT{t.duration_ms / 1000:g}S"
        artists = [
            {"id": f"art-{track_id}-{i}", "type": "artists"}
            for i in range(len(t.artists))
        ]
        return {
            "id": track_id,
            "type": "tracks",
            "attributes": attrs,
            "relationships": {
                "artists": {"data": artists, "links": {"self": "/x"}},
                "albums": {
                    "data": [{"id": f"alb-{track_id}", "type": "albums"}],
                    "links": {"self": "/x"},
                },
            },
        }

    def _video_resource(self, vid: str) -> dict[str, Any]:
        return {
            "id": vid,
            "type": "videos",
            "attributes": {
                "title": self.videos[vid],
                "duration": "PT3M",
                "explicit": False,
            },
            "relationships": {
                "artists": {"data": [], "links": {"self": "/x"}},
                "albums": {"data": [], "links": {"self": "/x"}},
            },
        }

    def _related(self, track_id: str, deep: bool) -> list[dict[str, Any]]:
        t = self.catalog[track_id]
        out: list[dict[str, Any]] = [
            {
                "id": f"art-{track_id}-{i}",
                "type": "artists",
                "attributes": {"name": name},
            }
            for i, name in enumerate(t.artists)
        ]
        album: dict[str, Any] = {
            "id": f"alb-{track_id}",
            "type": "albums",
            "attributes": {"title": t.album or "Album"},
        }
        if deep:
            album["relationships"] = {
                "coverArt": {
                    "data": [{"id": f"cov-{track_id}", "type": "artworks"}],
                    "links": {"self": "/x"},
                }
            }
            out.append(
                {
                    "id": f"cov-{track_id}",
                    "type": "artworks",
                    "attributes": {
                        "mediaType": "IMAGE",
                        "files": [
                            {
                                "href": f"https://{ART_HOST}/images/{track_id}/80x80.jpg",
                                "meta": {"width": 80, "height": 80},
                            }
                        ],
                    },
                }
            )
        out.append(album)
        return out

    def _me(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": {
                    "id": self.user_id,
                    "type": "users",
                    "attributes": {
                        "username": self.username,
                        "country": self.country,
                        "email": "sam@example.com",
                        "developerAccessTier": "THIRD_PARTY",
                    },
                },
                "links": {"self": "/users/me"},
            },
        )

    def _playlist_resource(self, p: _Playlist) -> dict[str, Any]:
        count = len(p.items) + (1 if p.pid in self._truncated else 0)
        attrs: dict[str, Any] = {
            "name": p.name,
            "accessType": p.access,
            "bounded": True,
            "createdAt": _EPOCH.isoformat(),
            "lastModifiedAt": p.last_modified,
            "numberOfItems": count,
            "numberOfFollowers": 0,
            "playlistType": "USER",
            "externalLinks": [],
        }
        if p.description:
            attrs["description"] = p.description
        return {"id": p.pid, "type": "playlists", "attributes": attrs}

    def _list_playlists(self, request: httpx.Request) -> httpx.Response:
        if request.url.params.get("filter[owners.id]") != "me":
            return _error(400, "a filter is required")
        start = int(request.url.params.get("page[cursor]", "0"))
        mine = list(self.playlists.values())
        page = mine[start : start + PAGE]
        links: dict[str, str] = {"self": "/playlists"}
        if start + PAGE < len(mine):
            links["next"] = _next(request, start + PAGE, bare=self.bare_next_links)
        return httpx.Response(
            200,
            json={"data": [self._playlist_resource(p) for p in page], "links": links},
        )

    def _get_playlist(self, request: httpx.Request, pid: str) -> httpx.Response:
        p = self.playlists.get(pid)
        if p is None:
            return _error(404, "Playlist not found")
        return httpx.Response(
            200,
            json={
                "data": self._playlist_resource(p),
                "links": {"self": f"/playlists/{pid}"},
            },
        )

    def _get_items(self, request: httpx.Request, pid: str) -> httpx.Response:
        p = self.playlists.get(pid)
        if p is None:
            return _error(404, "Playlist not found")
        if not self._country_ok(request):
            return _error(400, "countryCode is required")
        includes = self._includes(request, _ITEM_INCLUDES)
        if includes is None:
            return _error(400, "unknown include path")
        start = int(request.url.params.get("page[cursor]", "0"))
        page = p.items[start : start + PAGE]
        data = [
            {
                "id": i.id,
                "type": i.type,
                "meta": {"itemId": i.item_id, "addedAt": i.added_at.isoformat()},
            }
            for i in page
        ]
        included: list[dict[str, Any]] = []
        if includes:
            deep = "items.albums.coverArt" in includes
            for i in page:
                if i.item_id in self._unavailable:
                    continue
                if i.type == "videos":
                    included.append(self._video_resource(i.id))
                elif i.id in self.catalog:
                    included.append(self._track_resource(i.id))
                    included.extend(self._related(i.id, deep))
        links: dict[str, str] = {"self": f"/playlists/{pid}/relationships/items"}
        if start + PAGE < len(p.items):
            links["next"] = _next(request, start + PAGE, bare=self.bare_next_links)
        if start == 0 and pid in self._change_during_read:
            self._change_during_read.discard(pid)
            p.version += 1
        return httpx.Response(
            200, json={"data": data, "included": included, "links": links}
        )

    def _tracks_by_isrc(self, request: httpx.Request) -> httpx.Response:
        isrc = request.url.params.get("filter[isrc]")
        if not isrc or not self._country_ok(request):
            return _error(400, "filter[isrc] and countryCode are required")
        includes = self._includes(request, _TRACK_INCLUDES)
        if includes is None:
            return _error(400, "unknown include path")
        hits = [tid for tid, t in self.catalog.items() if t.isrc == isrc]
        included = [
            r for tid in hits for r in self._related(tid, "albums.coverArt" in includes)
        ]
        return httpx.Response(
            200,
            json={
                "data": [self._track_resource(tid) for tid in hits],
                "included": included if includes else [],
                "links": {"self": "/tracks"},
            },
        )

    def _search(self, request: httpx.Request, query: str) -> httpx.Response:
        if not self._country_ok(request):
            return _error(400, "countryCode is required")
        includes = self._includes(request, _SEARCH_INCLUDES)
        if includes is None:
            return _error(400, "unknown include path")
        words = query.lower().split()
        hits = [
            tid
            for tid, t in self.catalog.items()
            if all(w in f"{t.title} {' '.join(t.artists)}".lower() for w in words)
        ]
        included: list[dict[str, Any]] = []
        if includes:
            for tid in hits:
                included.append(self._track_resource(tid))
                included.extend(
                    self._related(tid, "tracks.albums.coverArt" in includes)
                )
        return httpx.Response(
            200,
            json={
                "data": [{"id": tid, "type": "tracks"} for tid in hits],
                "included": included,
                "links": {"self": "/searchResults"},
            },
        )

    # -- writes ----------------------------------------------------------

    def _create(self, request: httpx.Request) -> httpx.Response:
        body = _body(request)
        try:
            data = body["data"]
            attrs = data["attributes"]
            assert data["type"] == "playlists"
        except (KeyError, TypeError, AssertionError):
            return _error(400, "bad payload")
        problem = _check_details(attrs, create=True)
        if problem:
            return _error(400, problem)
        pid = self.add_playlist(attrs["name"], [])
        p = self.playlists[pid]
        p.description = attrs.get("description", "")
        p.access = attrs.get("accessType", "PUBLIC")
        return httpx.Response(
            201,
            json={
                "data": self._playlist_resource(p),
                "links": {"self": f"/playlists/{pid}"},
            },
        )

    def _update_playlist(self, request: httpx.Request, pid: str) -> httpx.Response:
        p = self.playlists.get(pid)
        if p is None:
            return _error(404, "Playlist not found")
        body = _body(request)
        try:
            data = body["data"]
            attrs = data["attributes"]
            assert data["type"] == "playlists" and data["id"] == pid
        except (KeyError, TypeError, AssertionError):
            return _error(400, "bad payload")
        problem = _check_details(attrs, create=False)
        if problem:
            return _error(400, problem)
        if "name" in attrs:
            p.name = attrs["name"]
        if "description" in attrs:
            p.description = attrs["description"]
        p.version += 1
        return httpx.Response(200, json={"data": self._playlist_resource(p)})

    def _refs(self, request: httpx.Request, limit: int) -> list[dict[str, Any]] | None:
        body = _body(request)
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, list) or not 1 <= len(data) <= limit:
            return None
        if any(d.get("type") not in ("tracks", "videos") for d in data):
            return None
        return data

    def _add_items(self, request: httpx.Request, pid: str) -> httpx.Response:
        p = self.playlists.get(pid)
        if p is None:
            return _error(404, "Playlist not found")
        refs = self._refs(request, 50)
        if refs is None:
            return _error(400, "1 to 50 items")
        added: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for ref in refs:
            known = ref["id"] in (
                self.videos if ref["type"] == "videos" else self.catalog
            )
            if not known:
                skipped.append(
                    {"id": ref["id"], "type": ref["type"], "reason": "NOT_FOUND"}
                )
                continue
            item = _Item(ref["type"], ref["id"], self._next_item_id(), _EPOCH)
            p.items.append(item)
            added.append(
                {
                    "id": item.id,
                    "type": item.type,
                    "meta": {"itemId": item.item_id, "addedAt": _EPOCH.isoformat()},
                }
            )
        p.version += 1
        return httpx.Response(
            200,
            json={"data": added, "meta": {"skipped": skipped}, "links": {"self": "/x"}},
        )

    def _selected(self, p: _Playlist, refs: list[dict[str, Any]]) -> list[_Item] | None:
        by_item = {i.item_id: i for i in p.items}
        out: list[_Item] = []
        for ref in refs:
            item = by_item.get(str((ref.get("meta") or {}).get("itemId")))
            if item is None or item.id != ref["id"] or item.type != ref["type"]:
                return None
            out.append(item)
        return out

    def _remove_items(self, request: httpx.Request, pid: str) -> httpx.Response:
        p = self.playlists.get(pid)
        if p is None:
            return _error(404, "Playlist not found")
        refs = self._refs(request, 50)
        if refs is None:
            return _error(400, "1 to 50 items")
        selected = self._selected(p, refs)
        if selected is None:
            return _error(422, "item not in playlist")
        gone = {i.item_id for i in selected}
        p.items = [i for i in p.items if i.item_id not in gone]
        p.version += 1
        return httpx.Response(200, json={"meta": {}})

    def _move_items(self, request: httpx.Request, pid: str) -> httpx.Response:
        p = self.playlists.get(pid)
        if p is None:
            return _error(404, "Playlist not found")
        refs = self._refs(request, 20)
        if refs is None:
            return _error(400, "1 to 20 items")
        selected = self._selected(p, refs)
        if selected is None:
            return _error(422, "item not in playlist")
        before = ((_body(request) or {}).get("meta") or {}).get("positionBefore")
        moving = {i.item_id for i in selected}
        if before in moving:
            return _error(400, "positionBefore is one of the moved items")
        rest = [i for i in p.items if i.item_id not in moving]
        if before is None:
            at = len(rest)
        else:
            positions = [n for n, i in enumerate(rest) if i.item_id == before]
            if not positions:
                return _error(422, "positionBefore not in playlist")
            at = positions[0]
        p.items = rest[:at] + selected + rest[at:]
        p.version += 1
        return httpx.Response(200, json={"meta": {}})


def _check_details(attrs: Mapping[str, Any], *, create: bool) -> str | None:
    name = attrs.get("name")
    if create and name is None:
        return "name is required"
    if name is not None and not 1 <= len(name) <= 250:
        return "name must be 1 to 250 characters"
    if len(attrs.get("description") or "") > 500:
        return "description must be at most 500 characters"
    if attrs.get("accessType", "PUBLIC") not in ("PUBLIC", "UNLISTED"):
        return "bad accessType"
    return None


def _next(request: httpx.Request, at: int, *, bare: bool = False) -> str:
    params = {} if bare else dict(request.url.params)
    params["page[cursor]"] = str(at)
    path = request.url.path.removeprefix("/v2")
    return str(httpx.URL(path, params=params))


def _error(
    status: int, detail: str, headers: Mapping[str, str] | None = None
) -> httpx.Response:
    return httpx.Response(
        status,
        json={
            "errors": [{"status": str(status), "code": "SIMULATED", "detail": detail}]
        },
        headers=dict(headers or {}),
    )


def _body(request: httpx.Request) -> Any:
    try:
        return _json.loads(request.content or b"null")
    except ValueError:
        return None
