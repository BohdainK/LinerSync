import pytest

from linersync.adapters.fake.connector import FakeConnector
from linersync.application.suggestions import SUGGEST_FLOOR, bare_title, search_text
from linersync.domain.capabilities import Capability
from linersync.domain.matching.types import MatchConfig
from linersync.domain.tracks import ServiceId
from tests.factories import track


@pytest.mark.parametrize(
    ("title", "bare"),
    [
        ("Song (Remastered 2011)", "Song"),
        ("Song [Live] - Radio Edit", "Song"),
        ("Song", "Song"),
        ("(Intro)", "(Intro)"),
    ],
)
def test_bare_title(title: str, bare: str) -> None:
    assert bare_title(title) == bare


async def test_search_text_keeps_low_scores() -> None:
    source = track("fake_a", "s", title="Song B")
    far = track("fake_b", "X1", title="Song B live remix", artists=("Other",))
    target = FakeConnector(
        ServiceId("fake_b"), "Fake B", frozenset(Capability), catalog=[far]
    )
    found = await search_text(source, target, MatchConfig(), "Song B")
    assert [c.track.track_id for c in found] == ["X1"]
    assert found[0].confidence < SUGGEST_FLOOR
