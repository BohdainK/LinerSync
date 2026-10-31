"""httpx transport that records exchanges to JSON files with secrets redacted."""

import json
import re
from collections.abc import Collection, Iterable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode

import httpx

DEFAULT_REDACT = frozenset(
    {
        "authorization",
        "music-user-token",
        "access_token",
        "refresh_token",
        "code",
        "code_verifier",
        "client_secret",
        "api_key",
        "email",
        "display_name",
        "firstName",
        "lastName",
        "username",
        "cookie",
        "device_code",
        "user_code",
        "accountName",
        "channelHandle",
        "visitorData",
        "datasyncId",
        "user_auth_token",
        "login",
    }
)
_REDACTED = "REDACTED"


def redact(obj: Any, keys: Collection[str]) -> Any:
    lowered = {k.lower() for k in keys}
    if isinstance(obj, dict):
        return {
            k: _REDACTED
            if isinstance(k, str) and k.lower() in lowered
            else redact(v, lowered)
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [redact(v, lowered) for v in obj]
    return obj


def _slug(path: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", path).strip("-") or "root"


class RecordingTransport(httpx.AsyncBaseTransport):
    def __init__(
        self,
        inner: httpx.AsyncBaseTransport,
        out_dir: Path,
        *,
        redact_keys: Iterable[str] = DEFAULT_REDACT,
    ) -> None:
        self._inner = inner
        self._out_dir = out_dir
        self._keys = frozenset(k.lower() for k in redact_keys)
        self._n = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self._inner.handle_async_request(request)
        await response.aread()
        self._n += 1
        self._out_dir.mkdir(parents=True, exist_ok=True)
        name = f"{self._n:03d}-{request.method}-{_slug(request.url.path)}.json"
        record = {
            "request": self._request(request),
            "response": self._response(response),
        }
        (self._out_dir / name).write_text(json.dumps(record, indent=2, sort_keys=True))
        return response

    def _url(self, url: httpx.URL) -> str:
        pairs = [
            (k, _REDACTED if k.lower() in self._keys else v)
            for k, v in parse_qsl(url.query.decode(), keep_blank_values=True)
        ]
        return str(url.copy_with(query=urlencode(pairs).encode() if pairs else None))

    def _scrub(self, text: str) -> str:
        """Redact `key=value` and `"key": "value"` forms in free text."""
        for key in self._keys:
            k = re.escape(key)
            text = re.sub(rf"(?i)(?<![\w-]){k}=[^&\s]*", f"{key}={_REDACTED}", text)
            text = re.sub(
                rf'(?i)("{k}"\s*:\s*)"(?:[^"\\]|\\.)*"', rf'\1"{_REDACTED}"', text
            )
        return text

    def _request(self, request: httpx.Request) -> dict[str, Any]:
        out: dict[str, Any] = {
            "method": request.method,
            "url": self._url(request.url),
            "json": None,
        }
        ctype = request.headers.get("content-type", "")
        body = request.content
        if body and "json" in ctype:
            try:
                out["json"] = redact(json.loads(body), self._keys)
            except ValueError:
                out["json"] = {"text": self._scrub(body.decode(errors="replace"))}
        elif body and "x-www-form-urlencoded" in ctype:
            form = dict(
                parse_qsl(body.decode(errors="replace"), keep_blank_values=True)
            )
            out["json"] = redact(form, self._keys)
        elif body:
            out["json"] = {"text": self._scrub(body.decode(errors="replace"))}
        return out

    def _response(self, response: httpx.Response) -> dict[str, Any]:
        ctype = response.headers.get("content-type", "")
        out: dict[str, Any] = {
            "status": response.status_code,
            "headers": {
                k: response.headers[k]
                for k in ("content-type", "retry-after")
                if k in response.headers
            },
        }
        body = response.content
        if ctype.startswith("image/"):
            out["bytes"] = len(body)
        elif "json" in ctype:
            try:
                out["json"] = redact(json.loads(body), self._keys)
            except ValueError:
                out["text"] = self._scrub(body.decode(errors="replace"))
        else:
            out["text"] = self._scrub(body.decode(errors="replace"))
        return out
