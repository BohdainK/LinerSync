import pytest

from linersync.testing.contract import LAWS, ContractSkip
from tests.contracts.harnesses import HARNESSES


@pytest.mark.parametrize("law_name", sorted(LAWS))
@pytest.mark.parametrize("harness_name", sorted(HARNESSES))
async def test_connector_law(harness_name: str, law_name: str) -> None:
    harness = await HARNESSES[harness_name]()
    try:
        await LAWS[law_name](harness)
    except ContractSkip as skip:
        pytest.skip(str(skip))
