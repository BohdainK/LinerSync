"""Type-directed JSON codec for domain values (stored bodies, plan 3 D5)."""

import dataclasses
import json
import types
from collections.abc import Mapping
from datetime import datetime, timedelta
from enum import StrEnum
from functools import cache
from typing import Any, Literal, Union, get_args, get_origin, get_type_hints

TAG = "$type"


@cache
def _hints(cls: type) -> dict[str, Any]:
    return get_type_hints(cls)


def _is_union(tp: Any) -> bool:
    return get_origin(tp) in (Union, types.UnionType)


def _is_dataclass_type(tp: Any) -> bool:
    return isinstance(tp, type) and dataclasses.is_dataclass(tp)


def _supertype(tp: Any) -> Any:
    return getattr(tp, "__supertype__", None)


def _members(tp: Any) -> tuple[list[Any], bool]:
    args = get_args(tp)
    return [a for a in args if a is not type(None)], type(None) in args


def encode(tp: Any, value: Any) -> Any:
    if value is None:
        return None
    sup = _supertype(tp)
    if sup is not None:
        return encode(sup, value)
    if _is_union(tp):
        members, _ = _members(tp)
        classes = [m for m in members if _is_dataclass_type(m)]
        if len(members) == 1:
            return encode(members[0], value)
        if len(classes) < 2 or len(classes) != len(members):
            raise TypeError(f"unsupported union: {tp}")
        for cls in classes:
            if type(value) is cls:
                return {TAG: cls.__name__, **_encode_fields(cls, value)}
        raise TypeError(f"{type(value).__name__} is not a member of {tp}")
    origin = get_origin(tp)
    if origin is Literal:
        return value
    if origin is tuple or origin is list:
        item = get_args(tp)[0]
        return [encode(item, v) for v in value]
    if origin is dict or (origin is not None and _is_mapping(origin)):
        return {k: encode(get_args(tp)[1], v) for k, v in value.items()}
    if isinstance(tp, type):
        if issubclass(tp, StrEnum):
            return tp(value).value
        if tp is datetime:
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("naive datetime")
            return value.isoformat()
        if tp is timedelta:
            return value.total_seconds()
        if dataclasses.is_dataclass(tp):
            return _encode_fields(tp, value)
        if tp in (bool, int, float, str):
            return value
    raise TypeError(f"cannot encode {tp}")


def _is_mapping(origin: Any) -> bool:
    return isinstance(origin, type) and issubclass(origin, Mapping)


def _encode_fields(cls: type, value: Any) -> dict[str, Any]:
    hints = _hints(cls)
    return {
        f.name: encode(hints[f.name], getattr(value, f.name))
        for f in dataclasses.fields(cls)
    }


def decode(tp: Any, data: Any, *, strict: bool = False) -> Any:
    """Decode `data` as `tp`. Lenient by default (stored bodies). `strict`
    (imports only) checks every scalar's JSON type, rejects null for a
    non-Optional field and turns any failure into ValueError."""
    if not strict:
        return _decode(tp, data, False)
    try:
        return _decode(tp, data, True)
    except ValueError:
        raise
    except Exception as e:
        raise ValueError(f"{type(e).__name__}: {e}") from e


def _expect(data: Any, kinds: type | tuple[type, ...], tp: Any) -> None:
    allowed = kinds if isinstance(kinds, tuple) else (kinds,)
    if not isinstance(data, allowed) or (
        isinstance(data, bool) and bool not in allowed
    ):
        raise ValueError(f"expected {tp}, got {type(data).__name__}")


def _decode(tp: Any, data: Any, strict: bool) -> Any:
    if data is None:
        if strict and not (_is_union(tp) and _members(tp)[1]):
            raise ValueError(f"expected {tp}, got null")
        return None
    sup = _supertype(tp)
    if sup is not None:
        return _decode(sup, data, strict)
    if _is_union(tp):
        members, _ = _members(tp)
        if len(members) == 1:
            return _decode(members[0], data, strict)
        classes = {m.__name__: m for m in members if _is_dataclass_type(m)}
        if len(classes) != len(members) or len(classes) < 2:
            raise TypeError(f"unsupported union: {tp}")
        if not isinstance(data, dict) or data.get(TAG) not in classes:
            raise ValueError(f"missing or unknown {TAG} for {tp}")
        return _decode_fields(classes[data[TAG]], data, strict)
    origin = get_origin(tp)
    if origin is Literal:
        if strict and data not in get_args(tp):
            raise ValueError(f"expected {tp}, got {data!r}")
        return data
    if origin is tuple or origin is list:
        if strict:
            _expect(data, list, tp)
        items = (_decode(get_args(tp)[0], v, strict) for v in data)
        return tuple(items) if origin is tuple else list(items)
    if origin is dict or (origin is not None and _is_mapping(origin)):
        if strict:
            _expect(data, dict, tp)
        return {k: _decode(get_args(tp)[1], v, strict) for k, v in data.items()}
    if isinstance(tp, type):
        if issubclass(tp, StrEnum):
            if strict:
                _expect(data, str, tp)
            return tp(data)
        if tp is datetime:
            if strict:
                _expect(data, str, tp)
            out = datetime.fromisoformat(data)
            if out.tzinfo is None:
                raise ValueError("naive datetime")
            return out
        if tp is timedelta:
            if strict:
                _expect(data, (int, float), tp)
            return timedelta(seconds=data)
        if dataclasses.is_dataclass(tp):
            return _decode_fields(tp, data, strict)
        if tp in (bool, int, float, str):
            if strict:
                _expect(data, (int, float) if tp is float else tp, tp)
                if tp is float:
                    return float(data)
            return data
    raise TypeError(f"cannot decode {tp}")


def _decode_fields(cls: type, data: Any, strict: bool = False) -> Any:
    if not isinstance(data, dict):
        raise ValueError(f"expected an object for {cls.__name__}")
    hints = _hints(cls)
    kwargs: dict[str, Any] = {}
    for f in dataclasses.fields(cls):
        if not f.init:
            continue
        if f.name in data:
            kwargs[f.name] = _decode(hints[f.name], data[f.name], strict)
        elif (
            f.default is dataclasses.MISSING
            and f.default_factory is dataclasses.MISSING
        ):
            raise ValueError(f"{cls.__name__}: missing field {f.name!r}")
    return cls(**kwargs)


def dumps(tp: Any, value: Any) -> str:
    return json.dumps(encode(tp, value), separators=(",", ":"), ensure_ascii=False)


def loads(tp: Any, text: str) -> Any:
    return decode(tp, json.loads(text))
