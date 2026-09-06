"""Keep CLI/config tests independent of the operator's active Hive install."""

import pytest


@pytest.fixture(autouse=True)
def isolated_hive_config(tmp_path, monkeypatch):
    """Tests must neither follow nor change the desktop's persisted chief selection."""
    monkeypatch.setenv("HIVE_CONFIG_FILE", str(tmp_path / "config.env"))
