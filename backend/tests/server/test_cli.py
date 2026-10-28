import os
from pathlib import Path
from typing import Any

import pytest
import uvicorn

from linersync.server import cli


@pytest.mark.skipif(os.getuid() == 0, reason="root ignores folder modes")
def test_serve_exits_2_when_data_dir_unwritable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    data = tmp_path / "ro"
    data.mkdir(mode=0o500)

    def fail(*args: Any, **kwargs: Any) -> None:
        pytest.fail("uvicorn.run must not be called")

    monkeypatch.setattr(uvicorn, "run", fail)
    for key in [k for k in os.environ if k.startswith("LINERSYNC_")]:
        monkeypatch.delenv(key)
    monkeypatch.setenv("LINERSYNC_ADMIN_PASSWORD", "pw")
    monkeypatch.setenv("LINERSYNC_DATA_DIR", str(data))
    try:
        assert cli.main(["serve"]) == 2
    finally:
        data.chmod(0o700)
    assert capsys.readouterr().err.startswith(
        "linersync: can't write to the data folder"
    )
