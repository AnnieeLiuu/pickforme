import pytest


@pytest.fixture(autouse=True)
def force_test_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """確保所有測試執行期間 ENVIRONMENT 恆為 test，杜絕任何測試呼叫或 Trace 洩漏至線上 Langfuse。"""
    monkeypatch.setenv("ENVIRONMENT", "test")
