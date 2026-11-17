"""A tiny MCP "ticket" server that shows why agent write tools need idempotency keys.

Two write tools do the same job:
  - create_ticket_unsafe: writes every time it is called.
  - create_ticket:        takes an idempotency_key and returns the stored result on repeats.

Storage is Redis (set REDIS_URL) or an in-process fakeredis when REDIS_URL is not set.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

KEY_TTL_SECONDS = 24 * 60 * 60  # how long a key is remembered


def make_redis():
    url = os.getenv("REDIS_URL")
    if url:
        import redis.asyncio as redis

        return redis.from_url(url, decode_responses=True)
    import fakeredis

    return fakeredis.FakeAsyncRedis(decode_responses=True)


class FlakyNetwork:
    """Simulates a slow response: the write commits, then the reply is delayed.

    The client's timeout fires, it retries, and the server has already done the work.
    """

    def __init__(self, slow_first_calls: int = 0, delay_s: float = 0.0):
        self.remaining = slow_first_calls
        self.delay_s = delay_s

    async def maybe_delay(self) -> None:
        if self.remaining > 0:
            self.remaining -= 1
            await asyncio.sleep(self.delay_s)


def build_server(r=None, network: FlakyNetwork | None = None, namespace: str | None = None) -> FastMCP:
    r = r or make_redis()
    ns = namespace or f"demo-{uuid.uuid4().hex[:8]}"  # isolates each run in a shared Redis
    network = network or FlakyNetwork()
    mcp = FastMCP("tickets")

    async def _insert_ticket(title: str) -> dict:
        ticket_id = await r.incr(f"{ns}:tickets:next_id")
        ticket = {"id": ticket_id, "title": title, "created_at": time.time()}
        await r.rpush(f"{ns}:tickets", json.dumps(ticket))
        await r.incr(f"{ns}:tickets:executions")  # server-side truth, not the model's summary
        return ticket

    @mcp.tool(
        annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=False),
    )
    async def create_ticket_unsafe(title: str) -> dict:
        """Create a support ticket. NOT safe to retry."""
        ticket = await _insert_ticket(title)
        await network.maybe_delay()
        return ticket

    @mcp.tool(
        annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=True),
    )
    async def create_ticket(title: str, idempotency_key: str) -> dict:
        """Create a support ticket. Safe to retry with the same idempotency_key."""
        slot = f"{ns}:idem:create_ticket:{idempotency_key}"

        # Claim the key atomically. Only the first caller gets True.
        claimed = await r.set(slot, json.dumps({"status": "pending"}), nx=True, ex=KEY_TTL_SECONDS)
        if not claimed:
            stored = json.loads(await r.get(slot))
            if stored["status"] == "done":
                return {**stored["result"], "replayed": True}
            # First call is still running: tell the caller instead of writing again.
            return {"status": "in_progress", "replayed": True}

        try:
            ticket = await _insert_ticket(title)
        except Exception:
            await r.delete(slot)  # let a later retry try again
            raise
        await r.set(slot, json.dumps({"status": "done", "result": ticket}), ex=KEY_TTL_SECONDS)
        await network.maybe_delay()
        return {**ticket, "replayed": False}

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def count_tickets() -> dict:
        """How many tickets exist and how many write executions the server saw."""
        return {
            "tickets": await r.llen(f"{ns}:tickets"),
            "executions": int(await r.get(f"{ns}:tickets:executions") or 0),
        }

    return mcp


if __name__ == "__main__":
    build_server(namespace="tickets").run()  # stdio transport, e.g. for Claude Desktop or MCP Inspector
