import asyncio

import pytest

from finance.broker.robinhood_mcp import (
    RobinhoodMCPClient,
    TERMINATE_MCP_SESSION_ON_CLOSE,
)


def test_robinhood_transport_does_not_send_delete_on_close() -> None:
    # Robinhood's Trading MCP rejects the optional streamable-HTTP DELETE
    # termination request with HTTP 400 after otherwise successful calls.
    assert TERMINATE_MCP_SESSION_ON_CLOSE is False


def test_run_with_session_preserves_successful_operation_result(monkeypatch) -> None:
    client = RobinhoodMCPClient(auth_store="unused-test-auth.json")

    async def fake_with_session(operation):
        class FakeSession:
            pass

        return await operation(FakeSession())

    monkeypatch.setattr(client, "_with_session", fake_with_session)

    async def operation(session_client):
        assert session_client.session is not None
        return {"status": "ok"}

    result = asyncio.run(client.run_with_session(operation))
    assert result == {"status": "ok"}


def test_run_with_session_propagates_true_session_failure(monkeypatch) -> None:
    client = RobinhoodMCPClient(auth_store="unused-test-auth.json")

    async def failing_with_session(operation):
        raise RuntimeError("transport failed before operation certainty")

    monkeypatch.setattr(client, "_with_session", failing_with_session)

    async def operation(session_client):
        return {"status": "should-not-run"}

    with pytest.raises(RuntimeError, match="transport failed before operation certainty"):
        asyncio.run(client.run_with_session(operation))
