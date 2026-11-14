import pytest


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
