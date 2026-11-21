import pytest


@pytest.fixture(autouse=True)
def private_settings(tmp_path, monkeypatch):
    """Point the app's own settings (recent kits) at a throwaway folder for every test."""
    monkeypatch.setenv("KIT_CONFIG_DIR", str(tmp_path / "settings"))


@pytest.fixture
def kit(tmp_path):
    """A small kit folder with three different 'sounds' (any bytes will do for tests)."""
    folder = tmp_path / "kit"
    (folder / "Kicks").mkdir(parents=True)
    (folder / "Hats").mkdir()
    (folder / "Kicks" / "kick_01.wav").write_bytes(b"KICK" * 100)
    (folder / "Kicks" / "kick_02.wav").write_bytes(b"BOOM" * 100)
    (folder / "Hats" / "hat_01.wav").write_bytes(b"TSS" * 100)
    return folder
