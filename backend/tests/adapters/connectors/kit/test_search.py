from linersync.adapters.connectors.kit.search import merge_hits
from tests.factories import track


def test_isrc_hits_first_then_new_term_hits() -> None:
    a, b, c = (track("tidal", i) for i in "abc")
    found = merge_hits([a, b], [b, c, a])
    assert [t.track_id for t in found] == ["a", "b", "c"]


def test_either_side_may_be_empty() -> None:
    a = track("tidal", "a")
    assert merge_hits([], [a]) == [a]
    assert merge_hits([a], []) == [a]
