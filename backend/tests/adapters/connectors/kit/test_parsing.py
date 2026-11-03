from linersync.adapters.connectors.kit.parsing import display_image


def test_display_image_smallest_at_least_display_size() -> None:
    images = [(640, "big"), (64, "tiny"), (300, "mid")]
    assert display_image(images) == "mid"


def test_display_image_falls_back_to_largest() -> None:
    assert display_image([(64, "tiny"), (160, "small")]) == "small"
    assert display_image([]) is None
