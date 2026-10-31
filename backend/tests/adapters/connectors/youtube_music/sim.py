"""A stateful YouTube Music (youtubei/v1) simulator on httpx.MockTransport.

Serves ``music.youtube.com`` in the shapes recorded under
tests/fixtures/youtube_music, Google's OAuth endpoints and an image host.
Accepts either a cookie with a valid SAPISIDHASH or a bearer token. Faults can
be injected per endpoint.
"""

import hashlib
import io
import json as _json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx
from PIL import Image

from linersync.domain.tracks import Track

API_HOST = "music.youtube.com"
GOOGLE_HOST = "oauth2.googleapis.com"
ART_HOST = "lh3.googleusercontent.com"
ORIGIN = "https://music.youtube.com"
ATV = "MUSIC_VIDEO_TYPE_ATV"
OMV = "MUSIC_VIDEO_TYPE_OMV"
SONGS_FILTER = "EgWKAQIIAWoMEA4QChADEAQQCRAF"
PAGE = 100
LIBRARY_PAGE = 25
_ISRC = re.compile(r"[A-Z]{2}[A-Z0-9]{3}\d{7}")


def _jpeg(seed: str, size: int = 60) -> bytes:
    digest = hashlib.sha256(seed.encode()).digest()
    img = Image.new("RGB", (size, size), (digest[0], digest[1], digest[2]))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _mmss(ms: int | None) -> str:
    seconds = (ms or 0) // 1000
    return f"{seconds // 60}:{seconds % 60:02d}"


@dataclass
class _Item:
    video_id: str
    set_id: str


@dataclass
class _Playlist:
    pid: str
    name: str
    description: str
    privacy: str
    items: list[_Item]
    owned: bool = True


@dataclass
class _Fault:
    endpoint: str  # "browse", "search", "browse/edit_playlist", ...
    times: int
    apply_first: bool
    status: int | None  # None: raise httpx.ReadTimeout
    skip: int = 0


@dataclass
class DeviceFlow:
    """Google's side of a device-code sign-in: what each token poll answers
    ("ok" issues tokens; anything else is the OAuth error Google returns)."""

    outcomes: list[str] = field(default_factory=lambda: ["authorization_pending"])


class YouTubeMusicSim:
    def __init__(
        self,
        *,
        catalog: Sequence[Track],
        isrcs: Mapping[str, str] | None = None,
        video_types: Mapping[str, str] | None = None,
        sapisid: str = "sapisid-value",
        access_token: str = "yt-access",
        client_id: str = "123-abc.apps.googleusercontent.com",
        client_secret: str = "GOCSPX-secret",
        account_name: str = "Sam Example",
    ) -> None:
        self.catalog = {t.track_id: t for t in catalog}
        self.isrcs = dict(isrcs or {})
        self.video_types = dict(video_types or {})
        self.sapisid = sapisid
        self.cookie = (
            f"SID=sid; HSID=hsid; SAPISID={sapisid}; __Secure-3PAPISID={sapisid}"
        )
        self.access_token = access_token
        self.client_id = client_id
        self.client_secret = client_secret
        self.account_name = account_name
        self.playlists: dict[str, _Playlist] = {}
        self.requests: list[httpx.Request] = []
        self.device = DeviceFlow()
        self._valid_tokens = {access_token}
        self._cookie_valid = True
        self._grant_dead = False
        self._faults: list[_Fault] = []
        self._truncated: set[str] = set()
        self._change_during_read: set[str] = set()
        self._greyed: set[str] = set()  # set ids
        self._fail_status_next = 0
        # (pid, after this continuation start, move from, move to)
        self._moves_during_read: list[tuple[str, int, int, int]] = []
        # Whether an empty playlist's header says how many songs it holds.
        self.empty_has_count = True
        self._count = 0
        self.transport = httpx.MockTransport(self.handle)

    # -- arrangement -----------------------------------------------------

    def _set_id(self) -> str:
        self._count += 1
        return f"{self._count:016X}"

    def add_playlist(
        self, name: str, track_ids: Sequence[str], *, owned: bool = True
    ) -> str:
        pid = f"PLsim{len(self.playlists) + 1:027d}"
        items = [_Item(t, self._set_id()) for t in track_ids]
        self.playlists[pid] = _Playlist(pid, name, "", "PRIVATE", items, owned)
        return pid

    def track_ids(self, pid: str) -> list[str]:
        return [i.video_id for i in self.playlists[pid].items]

    def set_ids(self, pid: str) -> list[str]:
        return [i.set_id for i in self.playlists[pid].items]

    def grey_out(self, pid: str, position: int) -> None:
        self._greyed.add(self.playlists[pid].items[position].set_id)

    # -- faults ----------------------------------------------------------

    def fail_next(
        self,
        endpoint: str,
        status: int,
        *,
        times: int = 1,
        apply_first: bool = False,
        skip: int = 0,
    ) -> None:
        self._faults.append(_Fault(endpoint, times, apply_first, status, skip))

    def timeout_next(self, endpoint: str, *, apply_first: bool, times: int = 1) -> None:
        self._faults.append(_Fault(endpoint, times, apply_first, None))

    def fail_status_next(self) -> None:
        """The next edit answers 200 with STATUS_FAILED and changes nothing."""
        self._fail_status_next += 1

    def truncate(self, pid: str) -> None:
        """The header reports one more song than the pages hold."""
        self._truncated.add(pid)

    def change_during_read(self, pid: str) -> None:
        """A song is added at the top right after the next first page."""
        self._change_during_read.add(pid)

    def move_during_read(self, pid: str, *, after: int, src: int, dst: int) -> None:
        """Move one song right after the continuation page starting at
        ``after`` is served: later pages shift, the first page doesn't."""
        self._moves_during_read.append((pid, after, src, dst))

    def expire_login(self) -> None:
        self._grant_dead = True
        self._cookie_valid = False
        self._valid_tokens.clear()

    # -- transport -------------------------------------------------------

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host = request.url.host
        if host == GOOGLE_HOST:
            return self._google(request)
        if host != API_HOST:
            return self._image(request)
        endpoint = request.url.path.removeprefix("/youtubei/v1/")
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
            if fault.endpoint == endpoint:
                if fault.skip > 0:
                    fault.skip -= 1
                    return None
                fault.times -= 1
                if fault.times <= 0:
                    self._faults.remove(fault)
                return fault
        return None

    def _authorised(self, request: httpx.Request) -> bool:
        auth = request.headers.get("authorization", "")
        if auth.startswith("Bearer "):
            return auth.removeprefix("Bearer ") in self._valid_tokens
        match = re.fullmatch(r"SAPISIDHASH (\d+)_([0-9a-f]{40})", auth)
        cookie = request.headers.get("cookie", "")
        if match is None or f"SAPISID={self.sapisid}" not in cookie:
            return False
        if request.headers.get("x-origin") != ORIGIN:
            return False
        ts, digest = match.groups()
        expected = hashlib.sha1(f"{ts} {self.sapisid} {ORIGIN}".encode()).hexdigest()
        return self._cookie_valid and digest == expected

    def _route(self, request: httpx.Request, endpoint: str) -> httpx.Response:
        if request.method != "POST" or request.url.params.get("prettyPrint") != "false":
            return _error(400, "bad request")
        body = _body(request)
        client = ((body or {}).get("context") or {}).get("client") or {}
        if client.get("clientName") != "WEB_REMIX" or not client.get("clientVersion"):
            return _error(400, "missing client context")
        if not self._authorised(request):
            return _error(401, "Request had invalid authentication credentials")
        routes: dict[str, Callable[[dict[str, Any]], httpx.Response]] = {
            "browse": self._browse,
            "search": self._search,
            "account/account_menu": self._account,
            "playlist/create": self._create,
            "browse/edit_playlist": self._edit,
        }
        handler = routes.get(endpoint)
        if handler is None:
            return _error(404, "Not found")
        return handler(body)

    # -- google / images ---------------------------------------------------

    def _google(self, request: httpx.Request) -> httpx.Response:
        form = dict(httpx.QueryParams(request.content.decode()))
        if request.url.path == "/device/code":
            if form.get("client_id") != self.client_id:
                return httpx.Response(401, json={"error": "invalid_client"})
            return httpx.Response(
                200,
                json={
                    "device_code": "device-code-1",
                    "user_code": "ABCD-EFGH",
                    "verification_url": "https://www.google.com/device",
                    "expires_in": 1800,
                    "interval": 5,
                },
            )
        if request.url.path != "/token":
            return _error(404, "Not found")
        if (form.get("client_id"), form.get("client_secret")) != (
            self.client_id,
            self.client_secret,
        ):
            return httpx.Response(401, json={"error": "invalid_client"})
        grant = form.get("grant_type")
        if grant == "urn:ietf:params:oauth:grant-type:device_code":
            outcome = (
                self.device.outcomes.pop(0)
                if len(self.device.outcomes) > 1
                else self.device.outcomes[0]
            )
            if outcome != "ok":
                status = {
                    "authorization_pending": 428,
                    "slow_down": 403,
                    "access_denied": 403,
                }
                return httpx.Response(status.get(outcome, 400), json={"error": outcome})
            return self._new_token(refresh=True)
        if grant == "refresh_token":
            if self._grant_dead:
                return httpx.Response(400, json={"error": "invalid_grant"})
            return self._new_token(refresh=False)
        return httpx.Response(400, json={"error": "unsupported_grant_type"})

    def _new_token(self, *, refresh: bool) -> httpx.Response:
        self._count += 1
        token = f"yt-access-{self._count}"
        self._valid_tokens.add(token)
        self.access_token = token
        body: dict[str, Any] = {
            "access_token": token,
            "expires_in": 3599,
            "token_type": "Bearer",
            "scope": "https://www.googleapis.com/auth/youtube",
        }
        if refresh:
            body["refresh_token"] = f"yt-refresh-{self._count}"
        return httpx.Response(200, json=body)

    def _image(self, request: httpx.Request) -> httpx.Response:
        if "authorization" in request.headers or "cookie" in request.headers:
            return _error(400, "credentials sent to an image host")
        return httpx.Response(
            200, content=_jpeg(str(request.url)), headers={"Content-Type": "image/jpeg"}
        )

    # -- rows --------------------------------------------------------------

    def _video_type(self, video_id: str) -> str:
        return self.video_types.get(video_id, ATV)

    def _title_column(self, video_id: str, *, playable: bool) -> dict[str, Any]:
        t = self.catalog.get(video_id)
        run: dict[str, Any] = {"text": t.title if t else f"Video {video_id}"}
        if playable:
            run["navigationEndpoint"] = {
                "watchEndpoint": {
                    "videoId": video_id,
                    "watchEndpointMusicSupportedConfigs": {
                        "watchEndpointMusicConfig": {
                            "musicVideoType": self._video_type(video_id)
                        }
                    },
                }
            }
        return {"musicResponsiveListItemFlexColumnRenderer": {"text": {"runs": [run]}}}

    @staticmethod
    def _linked(text: str, page_type: str, browse_id: str) -> dict[str, Any]:
        return {
            "text": text,
            "navigationEndpoint": {
                "browseEndpoint": {
                    "browseId": browse_id,
                    "browseEndpointContextSupportedConfigs": {
                        "browseEndpointContextMusicConfig": {"pageType": page_type}
                    },
                }
            },
        }

    def _thumbnail(self, video_id: str) -> dict[str, Any]:
        return {
            "musicThumbnailRenderer": {
                "thumbnail": {
                    "thumbnails": [
                        {
                            "url": f"https://{ART_HOST}/{video_id}=w60-h60",
                            "width": 60,
                            "height": 60,
                        }
                    ]
                }
            }
        }

    def _artist_runs(self, t: Track) -> list[dict[str, Any]]:
        runs: list[dict[str, Any]] = []
        for n, name in enumerate(t.artists):
            if n:
                runs.append({"text": " & "})
            runs.append(self._linked(name, "MUSIC_PAGE_TYPE_ARTIST", f"UC{name}"))
        return runs

    def _playlist_row(self, item: _Item) -> dict[str, Any]:
        playable = item.set_id not in self._greyed and item.video_id in self.catalog
        t = self.catalog.get(item.video_id)
        columns = [self._title_column(item.video_id, playable=playable)]
        if t is not None:
            columns.append(
                {
                    "musicResponsiveListItemFlexColumnRenderer": {
                        "text": {"runs": self._artist_runs(t)}
                    }
                }
            )
            album = (
                [self._linked(t.album, "MUSIC_PAGE_TYPE_ALBUM", "MPREb")]
                if t.album
                else []
            )
            columns.append(
                {"musicResponsiveListItemFlexColumnRenderer": {"text": {"runs": album}}}
            )
        row: dict[str, Any] = {
            "thumbnail": self._thumbnail(item.video_id),
            "flexColumns": columns,
            "fixedColumns": [
                {
                    "musicResponsiveListItemFixedColumnRenderer": {
                        "text": {
                            "runs": [{"text": _mmss(t.duration_ms if t else None)}]
                        }
                    }
                }
            ],
        }
        if playable:
            row["playlistItemData"] = {
                "playlistSetVideoId": item.set_id,
                "videoId": item.video_id,
            }
        else:
            row["musicItemRendererDisplayPolicy"] = (
                "MUSIC_ITEM_RENDERER_DISPLAY_POLICY_GREY_OUT"
            )
        return {"musicResponsiveListItemRenderer": row}

    def _search_row(self, video_id: str) -> dict[str, Any]:
        t = self.catalog[video_id]
        runs = self._artist_runs(t)
        if t.album:
            runs += [
                {"text": " • "},
                self._linked(t.album, "MUSIC_PAGE_TYPE_ALBUM", "MPREb"),
            ]
        runs += [{"text": " • "}, {"text": _mmss(t.duration_ms)}]
        return {
            "musicResponsiveListItemRenderer": {
                "thumbnail": self._thumbnail(video_id),
                "flexColumns": [
                    self._title_column(video_id, playable=True),
                    {
                        "musicResponsiveListItemFlexColumnRenderer": {
                            "text": {"runs": runs}
                        }
                    },
                    {
                        "musicResponsiveListItemFlexColumnRenderer": {
                            "text": {"runs": [{"text": "1M plays"}]}
                        }
                    },
                ],
            }
        }

    # -- browse ------------------------------------------------------------

    def _browse(self, body: dict[str, Any]) -> httpx.Response:
        token = body.get("continuation")
        if token:
            return self._continue(str(token))
        browse_id = str(body.get("browseId", ""))
        if browse_id == "FEmusic_liked_playlists":
            return self._library(0)
        if browse_id.startswith("VL"):
            return self._playlist_page(browse_id[2:])
        return _error(404, "Not found")

    def _library(self, start: int) -> httpx.Response:
        mine = list(self.playlists.values())
        page = mine[start : start + LIBRARY_PAGE]
        tiles: list[dict[str, Any]] = []
        if start == 0:
            tiles.append(
                {
                    "musicTwoRowItemRenderer": {
                        "title": {"runs": [{"text": "New playlist"}]},
                        "navigationEndpoint": {"createPlaylistEndpoint": {}},
                    }
                }
            )
        for p in page:
            count = len(p.items)
            tiles.append(
                {
                    "musicTwoRowItemRenderer": {
                        "title": {
                            "runs": [
                                {
                                    "text": p.name,
                                    "navigationEndpoint": {
                                        "browseEndpoint": {"browseId": f"VL{p.pid}"}
                                    },
                                }
                            ]
                        },
                        "subtitle": {
                            "runs": [
                                {"text": "Sam"},
                                {"text": " • "},
                                {"text": f"{count} song{'s' if count != 1 else ''}"},
                            ]
                        },
                        "navigationEndpoint": {
                            "browseEndpoint": {"browseId": f"VL{p.pid}"}
                        },
                    }
                }
            )
        grid: dict[str, Any] = {"items": tiles}
        if start + LIBRARY_PAGE < len(mine):
            grid["continuations"] = [
                {
                    "nextContinuationData": {
                        "continuation": f"lib:{start + LIBRARY_PAGE}"
                    }
                }
            ]
        if start == 0:
            doc = {
                "contents": {
                    "singleColumnBrowseResultsRenderer": {
                        "tabs": [
                            {
                                "tabRenderer": {
                                    "content": {
                                        "sectionListRenderer": {
                                            "contents": [{"gridRenderer": grid}]
                                        }
                                    }
                                }
                            }
                        ]
                    }
                }
            }
        else:
            doc = {"continuationContents": {"gridContinuation": grid}}
        return httpx.Response(200, json=doc)

    def _rows(self, p: _Playlist, start: int) -> list[dict[str, Any]]:
        rows = [self._playlist_row(i) for i in p.items[start : start + PAGE]]
        if start + PAGE < len(p.items):
            rows.append(
                {
                    "continuationItemRenderer": {
                        "continuationEndpoint": {
                            "continuationCommand": {
                                "token": f"pl:{p.pid}:{start + PAGE}",
                                "request": "CONTINUATION_REQUEST_TYPE_BROWSE",
                            }
                        }
                    }
                }
            )
        return rows

    def _playlist_page(self, pid: str) -> httpx.Response:
        p = self.playlists.get(pid)
        if p is None:
            return _error(404, "Requested entity was not found.")
        count = len(p.items) + (1 if pid in self._truncated else 0)
        header: dict[str, Any] = {
            "title": {"runs": [{"text": p.name}]},
            "subtitle": {
                "runs": [
                    {"text": "Playlist"},
                    {"text": " • "},
                    {"text": p.privacy.title()},
                ]
            },
        }
        if count or self.empty_has_count:
            noun = "song" if count == 1 else "songs"
            header["secondSubtitle"] = {"runs": [{"text": f"{count} {noun}"}]}
        if p.description:
            header["description"] = {
                "musicDescriptionShelfRenderer": {
                    "description": {"runs": [{"text": p.description}]}
                }
            }
        first: dict[str, Any] = (
            {
                "musicEditablePlaylistDetailHeaderRenderer": {
                    "header": {"musicResponsiveHeaderRenderer": header},
                    "editHeader": {
                        "musicPlaylistEditHeaderRenderer": {
                            "title": {"runs": [{"text": p.name}]},
                            "privacy": p.privacy,
                            "playlistId": pid,
                        }
                    },
                }
            }
            if p.owned
            else {"musicResponsiveHeaderRenderer": header}
        )
        doc = {
            "contents": {
                "twoColumnBrowseResultsRenderer": {
                    "tabs": [
                        {
                            "tabRenderer": {
                                "content": {
                                    "sectionListRenderer": {"contents": [first]}
                                }
                            }
                        }
                    ],
                    "secondaryContents": {
                        "sectionListRenderer": {
                            "contents": [
                                {
                                    "musicPlaylistShelfRenderer": {
                                        "playlistId": pid,
                                        "contents": self._rows(p, 0),
                                    }
                                }
                            ]
                        }
                    },
                }
            }
        }
        if pid in self._change_during_read:
            self._change_during_read.discard(pid)
            p.items.insert(0, _Item(next(iter(self.catalog)), self._set_id()))
        return httpx.Response(200, json=doc)

    def _continue(self, token: str) -> httpx.Response:
        if token.startswith("lib:"):
            return self._library(int(token[4:]))
        match = re.fullmatch(r"pl:([^:]+):(\d+)", token)
        p = self.playlists.get(match.group(1)) if match else None
        if match is None or p is None:
            return _error(400, "bad continuation")
        start = int(match.group(2))
        rows = self._rows(p, start)
        for move in [m for m in self._moves_during_read if m[:2] == (p.pid, start)]:
            self._moves_during_read.remove(move)
            p.items.insert(move[3], p.items.pop(move[2]))
        return httpx.Response(
            200,
            json={
                "onResponseReceivedActions": [
                    {"appendContinuationItemsAction": {"continuationItems": rows}}
                ]
            },
        )

    # -- search / account ----------------------------------------------------

    def _search(self, body: dict[str, Any]) -> httpx.Response:
        if body.get("params") != SONGS_FILTER:
            return _error(400, "expected the songs filter")
        query = str(body.get("query", ""))
        if _ISRC.fullmatch(query):
            # YouTube indexes ISRCs: the song first, then whatever else.
            hit = self.isrcs.get(query)
            others = [v for v in self.catalog if v != hit]
            hits = ([hit] if hit else []) + others[:1]
        else:
            words = query.lower().split()
            hits = [
                v
                for v, t in self.catalog.items()
                if all(w in f"{t.title} {' '.join(t.artists)}".lower() for w in words)
            ]
        shelf = {
            "musicShelfRenderer": {
                "title": {"runs": [{"text": "Songs"}]},
                "contents": [self._search_row(v) for v in hits],
            }
        }
        return httpx.Response(
            200,
            json={
                "contents": {
                    "tabbedSearchResultsRenderer": {
                        "tabs": [
                            {
                                "tabRenderer": {
                                    "content": {
                                        "sectionListRenderer": {"contents": [shelf]}
                                    }
                                }
                            }
                        ]
                    }
                }
            },
        )

    def _account(self, body: dict[str, Any]) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "actions": [
                    {
                        "openPopupAction": {
                            "popup": {
                                "multiPageMenuRenderer": {
                                    "header": {
                                        "activeAccountHeaderRenderer": {
                                            "accountName": {
                                                "runs": [{"text": self.account_name}]
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                ]
            },
        )

    # -- writes ------------------------------------------------------------

    def _create(self, body: dict[str, Any]) -> httpx.Response:
        title = body.get("title")
        if not isinstance(title, str) or not 1 <= len(title) <= 150:
            return _error(400, "title must be 1 to 150 characters")
        if len(body.get("description") or "") > 5000:
            return _error(400, "description too long")
        pid = self.add_playlist(title, [])
        p = self.playlists[pid]
        p.description = body.get("description") or ""
        p.privacy = str(body.get("privacyStatus") or "PUBLIC")
        return httpx.Response(200, json={"playlistId": pid})

    def _edit(self, body: dict[str, Any]) -> httpx.Response:
        p = self.playlists.get(str(body.get("playlistId")))
        if p is None or not p.owned:
            return _error(403, "You can't edit this playlist")
        actions = body.get("actions")
        if not isinstance(actions, list) or not actions:
            return _error(400, "no actions")
        if self._fail_status_next:
            self._fail_status_next -= 1
            return httpx.Response(200, json={"status": "STATUS_FAILED"})
        work = list(p.items)
        results: list[dict[str, Any]] = []
        for a in actions:
            kind = a.get("action")
            if kind == "ACTION_ADD_VIDEO":
                vid = a.get("addedVideoId")
                if vid not in self.catalog:
                    return _error(400, "bad add")
                # Without DEDUPE_OPTION_SKIP YouTube refuses a duplicate (it
                # asks the web user to confirm) and the whole edit fails.
                skip = a.get("dedupeOption") == "DEDUPE_OPTION_SKIP"
                if not skip and any(i.video_id == vid for i in work):
                    return httpx.Response(200, json={"status": "STATUS_FAILED"})
                item = _Item(str(vid), self._set_id())
                work.append(item)
                results.append(
                    {
                        "playlistEditVideoAddedResultData": {
                            "videoId": vid,
                            "setVideoId": item.set_id,
                        }
                    }
                )
            elif kind == "ACTION_REMOVE_VIDEO":
                found = [
                    i
                    for i in work
                    if i.set_id == a.get("setVideoId")
                    and i.video_id == a.get("removedVideoId")
                ]
                if not found:
                    return _error(400, "no such item")
                work.remove(found[0])
            elif kind == "ACTION_MOVE_VIDEO_BEFORE":
                moving = [i for i in work if i.set_id == a.get("setVideoId")]
                if not moving:
                    return _error(400, "no such item")
                work.remove(moving[0])
                successor = a.get("movedSetVideoIdSuccessor")
                if successor is None:
                    work.append(moving[0])
                else:
                    at = [n for n, i in enumerate(work) if i.set_id == successor]
                    if not at:
                        return _error(400, "no such successor")
                    work.insert(at[0], moving[0])
            elif kind == "ACTION_SET_PLAYLIST_NAME":
                name = a.get("playlistName")
                if not isinstance(name, str) or not 1 <= len(name) <= 150:
                    return _error(400, "bad name")
                p.name = name
            elif kind == "ACTION_SET_PLAYLIST_DESCRIPTION":
                text = a.get("playlistDescription")
                if not isinstance(text, str) or len(text) > 5000:
                    return _error(400, "bad description")
                p.description = text
            else:
                return _error(400, f"unknown action {kind}")
        p.items = work
        return httpx.Response(
            200, json={"status": "STATUS_SUCCEEDED", "playlistEditResults": results}
        )


def _error(status: int, message: str) -> httpx.Response:
    return httpx.Response(
        status,
        json={"error": {"code": status, "message": message, "status": "SIMULATED"}},
    )


def _body(request: httpx.Request) -> Any:
    try:
        return _json.loads(request.content or b"null")
    except ValueError:
        return None
