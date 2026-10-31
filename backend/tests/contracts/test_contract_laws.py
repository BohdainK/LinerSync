"""The laws themselves, for connectors whose tracks carry no ISRC."""

from dataclasses import replace

import pytest

from linersync.testing.contract import LAWS, ContractSkip
from tests.contracts.harnesses import FakeHarness


def _no_isrc_fake_harness() -> FakeHarness:
    h = FakeHarness()
    h.catalog = tuple(replace(t, isrc=None) for t in h.catalog)
    h.fake.catalog = h.catalog
    return h


async def test_isrc_law_skips_when_catalog_has_no_isrc() -> None:
    with pytest.raises(ContractSkip):
        await LAWS["search_by_isrc_finds_track"](_no_isrc_fake_harness())


async def test_metadata_law_runs_without_isrc() -> None:
    await LAWS["search_by_metadata_finds_track"](_no_isrc_fake_harness())
