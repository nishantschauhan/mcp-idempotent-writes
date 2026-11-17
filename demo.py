"""Run: python demo.py

Same flaky network, same retrying client, two write tools. Count what the server actually did.
"""

from __future__ import annotations

import asyncio
import logging

from mcp.shared.memory import create_connected_server_and_client_session

from client import call_with_retry, idempotency_key, result_json
from server import FlakyNetwork, build_server, make_redis

TIMEOUT_S = 0.5
SLOW_REPLY_S = 1.0  # first reply is slower than the client's timeout


async def scenario(tool: str, with_key: bool) -> dict:
    r = make_redis()
    server = build_server(r, FlakyNetwork(slow_first_calls=1, delay_s=SLOW_REPLY_S))
    args = {"title": "Printer on floor 3 is on fire"}
    if with_key:
        args["idempotency_key"] = idempotency_key("nishant", "turn-42", tool, {"title": args["title"]})

    async with create_connected_server_and_client_session(server._mcp_server) as session:
        await call_with_retry(session, tool, args, timeout_s=TIMEOUT_S)
        await asyncio.sleep(SLOW_REPLY_S)  # let the "lost" first call finish on the server
        counts = result_json(await session.call_tool("count_tickets", {}))
    return counts


async def main() -> None:
    print("\n[1] create_ticket_unsafe  (no idempotency key)")
    a = await scenario("create_ticket_unsafe", with_key=False)
    print(f"  server: {a['executions']} executions, {a['tickets']} tickets   <-- duplicate")

    print("\n[2] create_ticket  (idempotency key, stored result in Redis)")
    b = await scenario("create_ticket", with_key=True)
    print(f"  server: {b['executions']} execution,  {b['tickets']} ticket    <-- retry replayed the stored result")
    print()


if __name__ == "__main__":
    logging.disable(logging.INFO)  # hide SDK request logs
    asyncio.run(main())
