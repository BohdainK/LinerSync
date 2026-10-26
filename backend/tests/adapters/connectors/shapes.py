import json
from pathlib import Path
from typing import Any

_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


def key_paths(obj: Any) -> set[str]:
    """Dotted key paths of a JSON value; list items collapse to ``[]``."""
    paths: set[str] = set()

    def walk(value: Any, prefix: str) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                path = f"{prefix}.{key}" if prefix else str(key)
                paths.add(path)
                walk(child, path)
        elif isinstance(value, list):
            for child in value:
                walk(child, f"{prefix}[]")

    walk(obj, "")
    return paths


def load_fixture(service: str, name: str) -> Any:
    recorded = _FIXTURES / service / "recorded" / f"{name}.json"
    path = recorded if recorded.exists() else _FIXTURES / service / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8"))
