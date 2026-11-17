"""A client that behaves like many agent runtimes: on a timeout it retries the tool call."""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta

from mcp import ClientSession
from mcp.shared.exceptions import McpError


def idempotency_key(user: str, turn_id: str, tool: str, args: dict) -> str:
    """One key per *intended action*: same user, same turn, same tool, same normalised args."""
    canonical = json.dumps(args, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{user}|{turn_id}|{tool}|{canonical}".encode()).hexdigest()[:32]


def result_json(result) -> dict:
    """Tool results come back as JSON text content."""
    return json.loads(result.content[0].text)


async def call_with_retry(
    session: ClientSession,
    tool: str,
    args: dict,
    timeout_s: float = 0.5,
    max_attempts: int = 2,
    log=print,
):
    for attempt in range(1, max_attempts + 1):
        try:
            result = await session.call_tool(tool, args, read_timeout_seconds=timedelta(seconds=timeout_s))
            log(f"  attempt {attempt}: ok -> {result_json(result)}")
            return result
        except McpError as e:
            log(f"  attempt {attempt}: {e.error.message.splitlines()[0]} -> retrying")
    raise RuntimeError("all attempts failed")
