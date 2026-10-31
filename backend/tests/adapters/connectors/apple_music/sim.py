"""A stateful Apple Music API simulator on httpx.MockTransport.

Serves ``api.music.apple.com`` (the shapes recorded in tests/fixtures/apple_music)
and the artwork CDN. It models two Apple behaviours that matter for safety:

- library lag: a new playlist, and songs just added, become visible only after
  ``lag_reads`` further GETs of that playlist;
- an empty library playlist's ``/tracks`` answers 404 (D3).

The developer token must be an ES256 JWT signed with the simulator's own key
(``private_key_pem``); ``/v1/me/...`` also needs the Music-User-Token.

It also serves the web player (``music.apple.com``): a page linking a bundle
that embeds ``web_token``. That token is accepted only with the web player's
Origin, and stops working after ``rotate_web_token``.
"""

import hashlib
import io
import json as _json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from PIL import Image

from linersync.domain.tracks import Track

API_HOST = "api.music.apple.com"
WEB_HOST = "music.apple.com"
WEB_ORIGIN = "https://music.apple.com"
ART_HOST = "is1-ssl.mzstatic.com"
TEAM_ID = "TEAMSIM001"
KEY_ID = "KEYSIM0001"
_BASE_DATE = datetime(2026, 1, 1, tzinfo=UTC)
_MAX_PAGE = 100
_MAX_CATALOG_IDS = 300
_MAX_ADD = 100
_MAX_SEARCH = 25


def _jpeg(seed: str, size: int = 64) -> bytes:
    digest = hashlib.sha256(seed.encode()).digest()
    img = Image.new("RGB", (size, size), (digest[0], digest[1], digest[2]))
    for x in range(size // 2):
        img.putpixel((x, x), (digest[3], digest[4], digest[5]))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _iso(when: datetime) -> str:
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class _Playlist:
    pid: str
    name: str
    description: str
    can_edit: bool
    ids: list[str]
    version: int = 1
    modified: bool = True  # False: never changed, so no lastModifiedDate
    hidden: bool = False  # created, not yet visible
    stale: list[str] | None = None  # what GETs show while lagging
    lag_left: int = 0


@dataclass
class _Fault:
    method: str
    pattern: re.Pattern[str]
    times: int
    apply_first: bool
    status: int | None  # None: raise httpx.ReadTimeout
    json: Any = None
    headers: Mapping[str, str] = field(default_factory=dict)


class AppleMusicSim:
    def __init__(self, *, catalog: Sequence[Track], storefront: str = "us") -> None:
        # Digit ids are catalog songs; any other id is a library-only song.
        self.catalog = {t.track_id: t for t in catalog}
        self.storefront = storefront
        self.lag_reads = 0
        self.playlists: dict[str, _Playlist] = {}
        self.requests: list[httpx.Request] = []
        self.team_id = TEAM_ID
        self.key_id = KEY_ID
        key = ec.generate_private_key(ec.SECP256R1())
        self.private_key_pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode()
        self._public_key = key.public_key()
        self._web_tokens: list[str] = []
        self.web_tokens_accepted = True
        self.web_page_path = "/us/new"
        self.web_bundle_path = "/assets/index~sim0001.js"
        self.rotate_web_token()
        self.user_token = "sim-music-user-token"
        self._user_token_valid = True
        self._faults: list[_Fault] = []
        self._truncated: set[str] = set()
        self._change_during_read: set[str] = set()
        self._count = 0
        self.transport = httpx.MockTransport(self.handle)

    # -- arrangement -----------------------------------------------------

    def credentials(self, *, storefront: str | None = None) -> dict[str, str]:
        values = {
            "team_id": self.team_id,
            "key_id": self.key_id,
            "private_key": self.private_key_pem,
            "music_user_token": self.user_token,
            "music_user_token_saved_at": _iso(_BASE_DATE),
            "web_token": self.web_token,
        }
        if storefront is not None:
            values["storefront"] = storefront
        return values

    def add_playlist(
        self,
        name: str,
        catalog_ids: Sequence[str],
        *,
        can_edit: bool = True,
        description: str = "",
        last_modified: bool = True,
    ) -> str:
        """`last_modified=False`: a playlist never changed since it was made,
        which Apple serves without lastModifiedDate."""
        self._count += 1
        pid = f"p.sim{self._count:04d}"
        self.playlists[pid] = _Playlist(
            pid, name, description, can_edit, list(catalog_ids)
        )
        self.playlists[pid].modified = last_modified
        return pid

    def ids(self, playlist_id: str) -> list[str]:
        return list(self.playlists[playlist_id].ids)

    @property
    def web_token(self) -> str:
        return self._web_tokens[-1]

    def rotate_web_token(self) -> None:
        """The web player ships a new token; earlier ones are rejected."""
        n = len(self._web_tokens)
        claims = {
            "iss": "AMPWebPlay",
            "iat": int(_BASE_DATE.timestamp()) + n,
            "exp": int(datetime(2036, 1, 1, tzinfo=UTC).timestamp()),
        }
        self._web_tokens.append(
            jwt.encode(
                claims,
                self.private_key_pem,
                algorithm="ES256",
                headers={"kid": "WebPlayKid"},
            )
        )

    def revoke_user_token(self) -> None:
        self._user_token_valid = False

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
        """`path_regex` must fully match the URL path (including ``/v1``)."""
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
        """Report a total one higher than the songs served (until reset)."""
        self._truncated.add(playlist_id)

    def change_during_read(self, playlist_id: str) -> None:
        """Right after the next first tracks page is served, the playlist is
        reordered (same length) and its lastModifiedDate moves on."""
        self._change_during_read.add(playlist_id)

    # -- transport -------------------------------------------------------

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        fault = self._take_fault(request.method, path)
        if fault is None:
            return self._route(request, path)
        if fault.apply_first:
            self._route(request, path)
        if fault.status is None:
            raise httpx.ReadTimeout("simulated timeout", request=request)
        body = fault.json
        if body is None:
            body = _errors(fault.status, "Simulated failure")
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
        if request.url.host == ART_HOST:
            return self._image(request)
        if request.url.host == WEB_HOST:
            return self._web_player(path)
        if request.url.host != API_HOST:
            return httpx.Response(404)
        if not self._developer_token_ok(request):
            return _error(401, "Unauthorized", "The developer token is not valid")
        if path.startswith("/v1/me/") and not self._user_token_ok(request):
            return _error(403, "Forbidden", "Invalid or missing Music-User-Token")
        method = request.method
        lib = "/v1/me/library/playlists"
        routes: list[tuple[str, str, Callable[..., httpx.Response]]] = [
            ("GET", r"/v1/me/storefront", self._my_storefront),
            ("GET", r"/v1/storefronts/([^/]+)", self._storefront),
            ("GET", lib, self._list),
            ("POST", lib, self._create),
            ("GET", lib + r"/([^/]+)", self._get_playlist),
            ("GET", lib + r"/([^/]+)/tracks", self._get_tracks),
            ("POST", lib + r"/([^/]+)/tracks", self._add_tracks),
            ("GET", r"/v1/catalog/([^/]+)/songs", self._catalog_songs),
            ("GET", r"/v1/catalog/([^/]+)/search", self._search),
        ]
        for m, pattern, fn in routes:
            match = re.fullmatch(pattern, path)
            if m == method and match:
                return fn(request, *match.groups())
        return _error(404, "Resource Not Found", "No such resource")

    def _developer_token_ok(self, request: httpx.Request) -> bool:
        auth = request.headers.get("authorization", "")
        if not auth.startswith("Bearer "):
            return False
        token = auth.removeprefix("Bearer ")
        if token in self._web_tokens:
            origin = request.headers.get("origin")
            return (
                self.web_tokens_accepted
                and token == self.web_token
                and origin == WEB_ORIGIN
            )
        try:
            header = jwt.get_unverified_header(token)
            claims = jwt.decode(
                token,
                self._public_key,
                algorithms=["ES256"],
                options={"verify_exp": False},
            )
        except jwt.PyJWTError:
            return False
        return header.get("kid") == self.key_id and claims.get("iss") == self.team_id

    def _user_token_ok(self, request: httpx.Request) -> bool:
        token = request.headers.get("music-user-token")
        return self._user_token_valid and token == self.user_token

    # -- web player ------------------------------------------------------

    def _web_player(self, path: str) -> httpx.Response:
        if path == self.web_page_path:
            html = (
                '<html><head><script type="module" crossorigin '
                f'src="{self.web_bundle_path}"></script></head></html>'
            )
            return httpx.Response(200, text=html)
        if path == self.web_bundle_path:
            # Decoys: an expired web player JWT and another app's longer-lived
            # one, then the real one.
            expired = jwt.encode(
                {"exp": int(_BASE_DATE.timestamp())},
                self.private_key_pem,
                algorithm="ES256",
                headers={"kid": "WebPlayKid"},
            )
            other = jwt.encode(
                {"exp": int(datetime(2037, 1, 1, tzinfo=UTC).timestamp())},
                self.private_key_pem,
                algorithm="ES256",
                headers={"kid": "OTHERAPP01"},
            )
            js = (
                f'const a="{expired}";const o="{other}";'
                f'const t="{self.web_token}";export{{t}}'
            )
            return httpx.Response(200, text=js)
        return httpx.Response(404)

    # -- artwork ---------------------------------------------------------

    def _image(self, request: httpx.Request) -> httpx.Response:
        if "authorization" in request.headers or "music-user-token" in request.headers:
            return _error(400, "Bad Request", "credentials sent to an image host")
        if request.method != "GET":
            return httpx.Response(404)
        return httpx.Response(
            200,
            content=_jpeg(str(request.url)),
            headers={"Content-Type": "image/jpeg"},
        )

    # -- storefronts -----------------------------------------------------

    def _storefront_obj(self, sf: str) -> dict[str, Any]:
        return {
            "id": sf,
            "type": "storefronts",
            "href": f"/v1/storefronts/{sf}",
            "attributes": {
                "name": sf.upper(),
                "defaultLanguageTag": "en-US",
                "supportedLanguageTags": ["en-US"],
            },
        }

    def _my_storefront(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"data": [self._storefront_obj(self.storefront)]}
        )

    def _storefront(self, request: httpx.Request, sf: str) -> httpx.Response:
        if not re.fullmatch(r"[a-z]{2}", sf):
            return _error(404, "Resource Not Found", "Unknown storefront")
        return httpx.Response(200, json={"data": [self._storefront_obj(sf)]})

    # -- library playlists -----------------------------------------------

    def _playlist_obj(self, p: _Playlist) -> dict[str, Any]:
        attrs: dict[str, Any] = {
            "hasCatalog": False,
            "name": p.name,
            "canEdit": p.can_edit,
            "isPublic": False,
            "playParams": {"id": p.pid, "kind": "playlist", "isLibrary": True},
            "dateAdded": _iso(_BASE_DATE),
        }
        if p.description:
            attrs["description"] = {"standard": p.description}
        if p.modified:  # Apple omits it until the playlist is changed
            attrs["lastModifiedDate"] = _iso(_BASE_DATE + timedelta(seconds=p.version))
        return {
            "id": p.pid,
            "type": "library-playlists",
            "href": f"/v1/me/library/playlists/{p.pid}",
            "attributes": attrs,
        }

    def _seen(self, pid: str) -> tuple[_Playlist | None, list[str]]:
        """A GET of the playlist: returns it (None while hidden) and its visible
        songs, and counts down the lag."""
        p = self.playlists.get(pid)
        if p is None:
            return None, []
        lagging = p.lag_left > 0
        if lagging:
            p.lag_left -= 1
        if p.hidden:
            if lagging:
                return None, []
            p.hidden = False
        if lagging and p.stale is not None:
            return p, list(p.stale)
        p.stale = None
        return p, list(p.ids)

    def _list(self, request: httpx.Request) -> httpx.Response:
        offset, limit = _paging(request)
        if limit is None:
            return _error(400, "Bad Request", "Invalid limit or offset")
        visible = [p for p in self.playlists.values() if not p.hidden]
        chunk = visible[offset : offset + limit]
        body: dict[str, Any] = {
            "data": [self._playlist_obj(p) for p in chunk],
            "meta": {"total": len(visible)},
        }
        if offset + limit < len(visible):
            body["next"] = f"/v1/me/library/playlists?offset={offset + limit}"
        return httpx.Response(200, json=body)

    def _create(self, request: httpx.Request) -> httpx.Response:
        body = _body(request)
        attrs = body.get("attributes") if isinstance(body, dict) else None
        if not isinstance(attrs, dict) or not isinstance(attrs.get("name"), str):
            return _error(400, "Bad Request", "attributes.name is required")
        description = attrs.get("description", "")
        pid = self.add_playlist(
            attrs["name"], [], description=str(description or ""), last_modified=False
        )
        p = self.playlists[pid]
        if self.lag_reads > 0:
            p.hidden = True
            p.lag_left = self.lag_reads
        return httpx.Response(201, json={"data": [self._playlist_obj(p)]})

    def _get_playlist(self, request: httpx.Request, pid: str) -> httpx.Response:
        p, _ = self._seen(pid)
        if p is None:
            return _error(404, "Resource Not Found", "No such playlist")
        return httpx.Response(200, json={"data": [self._playlist_obj(p)]})

    def _get_tracks(self, request: httpx.Request, pid: str) -> httpx.Response:
        offset, limit = _paging(request)
        if limit is None:
            return _error(400, "Bad Request", "Invalid limit or offset")
        p, ids = self._seen(pid)
        if p is None:
            return _error(404, "Resource Not Found", "No such playlist")
        if not ids:
            # Apple answers 404 for an empty playlist's songs (D3).
            return _error(404, "Resource Not Found", "No related resources")
        chunk = ids[offset : offset + limit]
        if not chunk:
            return _error(404, "Resource Not Found", "No related resources")
        total = len(ids) + (1 if pid in self._truncated else 0)
        body: dict[str, Any] = {
            "data": [self.library_song_obj(i) for i in chunk],
            "meta": {"total": total},
        }
        if offset + limit < len(ids):
            body["next"] = (
                f"/v1/me/library/playlists/{pid}/tracks?offset={offset + limit}"
            )
        if offset == 0 and pid in self._change_during_read:
            self._change_during_read.discard(pid)
            p.ids = p.ids[1:] + p.ids[:1]
            self._modify(p)
        return httpx.Response(200, json=body)

    def _add_tracks(self, request: httpx.Request, pid: str) -> httpx.Response:
        p = self.playlists.get(pid)
        if p is None:
            return _error(404, "Resource Not Found", "No such playlist")
        if not p.can_edit:
            return _error(403, "Forbidden", "The playlist can't be edited")
        body = _body(request)
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, list) or not 1 <= len(data) <= _MAX_ADD:
            return _error(400, "Bad Request", "Send 1 to 100 songs")
        added: list[str] = []
        for item in data:
            resolved = self._resolve(item)
            if resolved is None:
                return _error(400, "Bad Request", f"Unknown song {item!r}")
            added.append(resolved)
        if p.lag_left == 0:
            p.stale = list(p.ids)
        p.lag_left = self.lag_reads
        p.ids.extend(added)
        self._modify(p)
        return httpx.Response(204)

    def _modify(self, p: _Playlist) -> None:
        """Every write moves lastModifiedDate on."""
        p.version += 1
        p.modified = True

    def _resolve(self, item: Any) -> str | None:
        if not isinstance(item, dict):
            return None
        ident, kind = item.get("id"), item.get("type")
        if not isinstance(ident, str):
            return None
        if kind == "songs":
            return ident if ident.isdigit() and ident in self.catalog else None
        if kind == "library-songs":
            if ident in self.catalog and not ident.isdigit():
                return ident
            catalog_id = ident.removeprefix("i.")
            if catalog_id.isdigit() and catalog_id in self.catalog:
                return catalog_id
        return None

    # -- song objects ----------------------------------------------------

    def _artwork(self, ident: str) -> dict[str, Any]:
        return {
            "width": 1200,
            "height": 1200,
            "url": f"https://{ART_HOST}/image/thumb/sim/{ident}/{{w}}x{{h}}bb.jpg",
        }

    def library_song_obj(self, track_id: str) -> dict[str, Any]:
        t = self.catalog[track_id]
        in_catalog = track_id.isdigit()
        lib_id = f"i.{track_id}" if in_catalog else track_id
        play: dict[str, Any] = {
            "id": lib_id,
            "kind": "song",
            "isLibrary": True,
            "reporting": in_catalog,
        }
        if in_catalog:
            play["catalogId"] = track_id
        attrs: dict[str, Any] = {
            "name": t.title,
            "durationInMillis": t.duration_ms,
            "hasLyrics": False,
            "trackNumber": 1,
            "playParams": play,
        }
        if t.artists:
            attrs["artistName"] = t.artists[0]
        if t.album:
            attrs["albumName"] = t.album
        if in_catalog:
            attrs["artwork"] = self._artwork(track_id)
        return {
            "id": lib_id,
            "type": "library-songs",
            "href": f"/v1/me/library/songs/{lib_id}",
            "attributes": attrs,
        }

    def catalog_song_obj(self, track_id: str) -> dict[str, Any]:
        t = self.catalog[track_id]
        attrs: dict[str, Any] = {
            "name": t.title,
            "durationInMillis": t.duration_ms,
            "url": f"https://music.apple.com/{self.storefront}/song/{track_id}",
            "artwork": self._artwork(track_id),
            "playParams": {"id": track_id, "kind": "song"},
        }
        if t.artists:
            attrs["artistName"] = t.artists[0]
        if t.album:
            attrs["albumName"] = t.album
        if t.isrc:
            attrs["isrc"] = t.isrc
        return {
            "id": track_id,
            "type": "songs",
            "href": f"/v1/catalog/{self.storefront}/songs/{track_id}",
            "attributes": attrs,
        }

    def _catalog_ids(self) -> list[str]:
        return [i for i in self.catalog if i.isdigit()]

    # -- catalog ---------------------------------------------------------

    def _catalog_songs(self, request: httpx.Request, sf: str) -> httpx.Response:
        if sf != self.storefront:
            return _error(404, "Resource Not Found", "Unknown storefront")
        params = request.url.params
        if "ids" in params:
            wanted = [i for i in params["ids"].split(",") if i]
            if not 1 <= len(wanted) <= _MAX_CATALOG_IDS:
                return _error(400, "Bad Request", "Send 1 to 300 ids")
            hits = [i for i in wanted if i.isdigit() and i in self.catalog]
        elif "filter[isrc]" in params:
            isrc = params["filter[isrc]"]
            hits = [i for i in self._catalog_ids() if self.catalog[i].isrc == isrc]
        else:
            return _error(400, "Bad Request", "ids or filter[isrc] is required")
        return httpx.Response(
            200, json={"data": [self.catalog_song_obj(i) for i in hits]}
        )

    def _search(self, request: httpx.Request, sf: str) -> httpx.Response:
        if sf != self.storefront:
            return _error(404, "Resource Not Found", "Unknown storefront")
        params = request.url.params
        term = params.get("term", "")
        try:
            limit = int(params.get("limit", "5"))
        except ValueError:
            return _error(400, "Bad Request", "Invalid limit")
        if params.get("types") != "songs" or not term or not 1 <= limit <= _MAX_SEARCH:
            return _error(400, "Bad Request", "Bad search request")
        needle = term.casefold()
        hits = [
            i for i in self._catalog_ids() if self.catalog[i].title.casefold() in needle
        ][:limit]
        if not hits:
            return httpx.Response(
                200,
                json={
                    "results": {},
                    "meta": {"results": {"order": [], "rawOrder": []}},
                },
            )
        href = str(
            httpx.URL(
                f"/v1/catalog/{sf}/search", params={"term": term, "types": "songs"}
            )
        )
        return httpx.Response(
            200,
            json={
                "results": {
                    "songs": {
                        "href": href,
                        "data": [self.catalog_song_obj(i) for i in hits],
                    }
                },
                "meta": {"results": {"order": ["songs"], "rawOrder": ["songs"]}},
            },
        )


def _errors(status: int, title: str, detail: str = "") -> dict[str, Any]:
    return {
        "errors": [
            {
                "id": "SIM",
                "title": title,
                "detail": detail or title,
                "status": str(status),
                "code": f"{status}00",
            }
        ]
    }


def _error(status: int, title: str, detail: str = "") -> httpx.Response:
    return httpx.Response(status, json=_errors(status, title, detail))


def _body(request: httpx.Request) -> Any:
    try:
        return _json.loads(request.content or b"null")
    except ValueError:
        return None


def _paging(request: httpx.Request) -> tuple[int, int | None]:
    params = request.url.params
    try:
        offset = int(params.get("offset", "0"))
        limit = int(params.get("limit", str(_MAX_PAGE)))
    except ValueError:
        return 0, None
    if offset < 0 or not 1 <= limit <= _MAX_PAGE:
        return 0, None
    return offset, limit
