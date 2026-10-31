import re

from tests.deploy.helpers import REPO

DOC_FILES = [REPO / "README.md", *sorted((REPO / "docs").glob("*.md"))]


def test_reverse_proxy_doc_contains_examples() -> None:
    doc = (REPO / "docs" / "reverse-proxy.md").read_text()
    for name in ("Caddyfile", "traefik.yml", "nginx.conf", "docker-compose.yml"):
        example = (REPO / "deploy" / "examples" / "own-proxy" / name).read_text()
        assert example in doc, name


def test_relative_links_resolve() -> None:
    checked = 0
    for path in DOC_FILES:
        for target in re.findall(r"\]\(([^)\s]+)\)", path.read_text()):
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            file_part = target.split("#", 1)[0]
            assert (path.parent / file_part).exists(), f"{path.name}: {target}"
            checked += 1
    assert checked > 0


def test_readme_covers_essentials() -> None:
    readme = (REPO / "README.md").read_text()
    for text in (
        "install.sh",
        "docker compose up -d",
        "/api/connections/oauth/callback",
        "PolyForm Noncommercial 1.0.0",
        "Apple Developer Program",
        "secret.key",
        "](docs/install.md",
        "](docs/reverse-proxy.md",
        "](docs/connectors.md",
        "](LICENSE",
    ):
        assert text in readme, text


def test_no_plan_references_in_docs() -> None:
    for path in DOC_FILES:
        assert "Plan 6" not in path.read_text(), path.name


def test_server_doc_links_setup_guides() -> None:
    doc = (REPO / "docs" / "server.md").read_text()
    assert "](install.md" in doc
    assert "](reverse-proxy.md" in doc
