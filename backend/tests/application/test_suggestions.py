import pytest

from linersync.application.suggestions import bare_title


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
