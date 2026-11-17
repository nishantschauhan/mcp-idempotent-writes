import asyncio
import logging
import sys
from pathlib import Path

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from client import call_with_retry, idempotency_key, result_json  # noqa: E402
from server import FlakyNetwork, build_server, make_redis  # noqa: E402

logging.disable(logging.INFO)
TITLE = {"title": "Printer on floor 3 is on fire"}


async def run(tool, args, slow_first_calls=1):
    server = build_server(make_redis(), FlakyNetwork(slow_first_calls=slow_first_calls, delay_s=0.6))
    async with create_connected_server_and_client_session(server._mcp_server) as s:
        await call_with_retry(s, tool, args, timeout_s=0.3, log=lambda *_: None)
        await asyncio.sleep(0.7)
        return result_json(await s.call_tool("count_tickets", {}))


@pytest.mark.asyncio
async def test_retry_without_key_creates_duplicate():
    counts = await run("create_ticket_unsafe", dict(TITLE))
    assert counts == {"tickets": 2, "executions": 2}


@pytest.mark.asyncio
async def test_retry_with_key_executes_once():
    key = idempotency_key("u1", "turn-1", "create_ticket", TITLE)
    counts = await run("create_ticket", {**TITLE, "idempotency_key": key})
    assert counts == {"tickets": 1, "executions": 1}


@pytest.mark.asyncio
async def test_replay_returns_same_ticket():
    server = build_server(make_redis())
    args = {**TITLE, "idempotency_key": "k-1"}
    async with create_connected_server_and_client_session(server._mcp_server) as s:
        first = result_json(await s.call_tool("create_ticket", args))
        second = result_json(await s.call_tool("create_ticket", args))
    assert first["id"] == second["id"]
    assert first["replayed"] is False and second["replayed"] is True


@pytest.mark.asyncio
async def test_concurrent_duplicates_execute_once():
    server = build_server(make_redis())
    args = {**TITLE, "idempotency_key": "k-2"}
    async with create_connected_server_and_client_session(server._mcp_server) as s:
        await asyncio.gather(*(s.call_tool("create_ticket", args) for _ in range(10)))
        counts = result_json(await s.call_tool("count_tickets", {}))
    assert counts["executions"] == 1


def test_key_ignores_argument_order():
    a = idempotency_key("u", "t", "create_ticket", {"title": "x", "priority": 1})
    b = idempotency_key("u", "t", "create_ticket", {"priority": 1, "title": "x"})
    assert a == b


@pytest.mark.asyncio
async def test_tool_annotations_tell_clients_what_is_safe_to_retry():
    server = build_server(make_redis())
    async with create_connected_server_and_client_session(server._mcp_server) as s:
        tools = {t.name: t.annotations for t in (await s.list_tools()).tools}
    assert tools["create_ticket"].idempotentHint is True
    assert tools["create_ticket_unsafe"].idempotentHint is False
    assert tools["count_tickets"].readOnlyHint is True
