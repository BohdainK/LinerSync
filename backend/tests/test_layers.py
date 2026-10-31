import ast
import sys
from collections.abc import Iterator
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parent.parent / "src"

FORBIDDEN = {
    "linersync.domain": (
        "linersync.ports",
        "linersync.application",
        "linersync.adapters",
        "linersync.server",
    ),
    "linersync.ports": (
        "linersync.application",
        "linersync.adapters",
        "linersync.server",
    ),
    "linersync.application": ("linersync.adapters", "linersync.server"),
    "linersync.adapters": ("linersync.server",),
    "linersync.adapters.sqlite": ("linersync.adapters.connectors",),
    "linersync.adapters.scheduler": ("linersync.adapters.connectors",),
    "linersync.adapters.covers": ("linersync.adapters.connectors",),
    "linersync.adapters.connectors.kit": (
        "linersync.adapters.connectors.spotify",
        "linersync.adapters.connectors.apple_music",
        "linersync.adapters.connectors.jellyfin",
        "linersync.adapters.connectors.tidal",
        "linersync.adapters.connectors.youtube_music",
        "linersync.adapters.connectors.qobuz",
        "linersync.application",
    ),
    "linersync.adapters.connectors.spotify": (
        "linersync.adapters.connectors.apple_music",
        "linersync.adapters.connectors.jellyfin",
        "linersync.adapters.connectors.tidal",
        "linersync.adapters.connectors.youtube_music",
        "linersync.adapters.connectors.qobuz",
        "linersync.application",
    ),
    "linersync.adapters.connectors.apple_music": (
        "linersync.adapters.connectors.spotify",
        "linersync.adapters.connectors.jellyfin",
        "linersync.adapters.connectors.tidal",
        "linersync.adapters.connectors.youtube_music",
        "linersync.adapters.connectors.qobuz",
        "linersync.application",
    ),
    "linersync.adapters.connectors.jellyfin": (
        "linersync.adapters.connectors.spotify",
        "linersync.adapters.connectors.apple_music",
        "linersync.adapters.connectors.tidal",
        "linersync.adapters.connectors.youtube_music",
        "linersync.adapters.connectors.qobuz",
        "linersync.application",
    ),
    "linersync.adapters.connectors.tidal": (
        "linersync.adapters.connectors.spotify",
        "linersync.adapters.connectors.apple_music",
        "linersync.adapters.connectors.jellyfin",
        "linersync.adapters.connectors.youtube_music",
        "linersync.adapters.connectors.qobuz",
        "linersync.application",
    ),
    "linersync.adapters.connectors.youtube_music": (
        "linersync.adapters.connectors.spotify",
        "linersync.adapters.connectors.apple_music",
        "linersync.adapters.connectors.jellyfin",
        "linersync.adapters.connectors.tidal",
        "linersync.adapters.connectors.qobuz",
        "linersync.application",
    ),
    "linersync.adapters.connectors.qobuz": (
        "linersync.adapters.connectors.spotify",
        "linersync.adapters.connectors.apple_music",
        "linersync.adapters.connectors.jellyfin",
        "linersync.adapters.connectors.tidal",
        "linersync.adapters.connectors.youtube_music",
        "linersync.application",
    ),
}
DOMAIN_FORBIDDEN_STDLIB = {
    "socket",
    "sqlite3",
    "http",
    "urllib",
    "subprocess",
    "asyncio",
    "shutil",
    "pathlib",
}


def _module_name(src_root: Path, path: Path) -> tuple[str, bool]:
    parts = list(path.relative_to(src_root).with_suffix("").parts)
    is_package = parts[-1] == "__init__"
    if is_package:
        parts.pop()
    return ".".join(parts), is_package


def _imports_of(tree: ast.AST, module: str, is_package: bool) -> list[str]:
    package = module if is_package else module.rpartition(".")[0]
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                anchor = package.split(".")
                anchor = anchor[: len(anchor) - (node.level - 1)]
                base = ".".join([*anchor, *([node.module] if node.module else [])])
            if base:
                found.append(base)
            found.extend(
                f"{base}.{a.name}" if base else a.name
                for a in node.names
                if a.name != "*"
            )
    return found


def _iter_imports(src_root: Path) -> Iterator[tuple[str, list[str]]]:
    for path in sorted(src_root.rglob("*.py")):
        module, is_package = _module_name(src_root, path)
        tree = ast.parse(path.read_text(), filename=str(path))
        yield module, _imports_of(tree, module, is_package)


def _is_within(name: str, prefix: str) -> bool:
    return name == prefix or name.startswith(prefix + ".")


def find_violations(src_root: Path) -> list[str]:
    violations: list[str] = []
    for module, imported in _iter_imports(src_root):
        for layer, forbidden in FORBIDDEN.items():
            if not _is_within(module, layer):
                continue
            for target in forbidden:
                if any(_is_within(name, target) for name in imported):
                    violations.append(f"{module} imports {target}")
    return violations


def test_layer_rules_hold() -> None:
    assert find_violations(SRC_ROOT) == []


def test_domain_is_stdlib_only() -> None:
    allowed = set(sys.stdlib_module_names) - DOMAIN_FORBIDDEN_STDLIB
    bad: list[str] = []
    for module, imported in _iter_imports(SRC_ROOT):
        if not (module == "linersync.domain" or module.startswith("linersync.domain.")):
            continue
        for name in imported:
            top = name.split(".")[0]
            if _is_within(name, "linersync.domain"):
                continue
            if top in allowed:
                continue
            bad.append(f"{module} imports {name}")
    assert bad == []


def test_checker_detects_violation(tmp_path: Path) -> None:
    (tmp_path / "linersync/domain").mkdir(parents=True)
    (tmp_path / "linersync/domain/bad.py").write_text(
        "from linersync.adapters import x\n"
    )
    assert find_violations(tmp_path) == [
        "linersync.domain.bad imports linersync.adapters"
    ]


def test_checker_detects_kit_importing_service_package(tmp_path: Path) -> None:
    (tmp_path / "linersync/adapters/connectors/kit").mkdir(parents=True)
    (tmp_path / "linersync/adapters/connectors/kit/bad.py").write_text(
        "from linersync.adapters.connectors.spotify import client\n"
    )
    assert find_violations(tmp_path) == [
        "linersync.adapters.connectors.kit.bad imports "
        "linersync.adapters.connectors.spotify"
    ]


def test_checker_detects_adapter_importing_server(tmp_path: Path) -> None:
    (tmp_path / "linersync/adapters").mkdir(parents=True)
    (tmp_path / "linersync/adapters/bad.py").write_text(
        "from linersync.server import app\n"
    )
    assert find_violations(tmp_path) == [
        "linersync.adapters.bad imports linersync.server"
    ]
