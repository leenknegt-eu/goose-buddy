import pytest


@pytest.fixture(autouse=True)
def buddy_home(tmp_path, monkeypatch):
    """Every test gets its own state dir and cannot see the real identity, key or sessions."""
    monkeypatch.setenv("GOOSE_BUDDY_HOME", str(tmp_path / "buddy"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("OPENCODE_API_KEY", raising=False)
    monkeypatch.delenv("GOOSE_BUDDY_USER_ID", raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    (tmp_path / "home").mkdir()
    return tmp_path / "buddy"
