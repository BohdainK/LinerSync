from tests.adapters.connectors.shapes import key_paths, load_fixture


def test_key_paths_collapses_list_items() -> None:
    entries = [{"item": {"name": "x"}}, {"item": {"uri": "u"}}]
    obj = {"items": {"total": 1, "items": entries}}
    assert key_paths(obj) == {
        "items",
        "items.total",
        "items.items",
        "items.items[].item",
        "items.items[].item.name",
        "items.items[].item.uri",
    }


def test_load_fixture_reads_plain_fixture() -> None:
    assert load_fixture("spotify", "me")["id"] == "me"
