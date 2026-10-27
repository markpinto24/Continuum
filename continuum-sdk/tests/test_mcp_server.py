"""The MCP tools, called through the real MCPServer with a client on a mock
transport. What the model reads is the product here: disputes and maybes must
never read as settled beliefs, and settle must refuse without the person's words."""

from __future__ import annotations

import httpx
import pytest

from continuum_sdk import AsyncContinuum
from continuum_sdk.mcp_server import build_server
from tests.test_client import api


@pytest.fixture
def server():  # noqa: ANN201
    client = AsyncContinuum("ck_test", "http://continuum.test", transport=httpx.MockTransport(api))
    return build_server(client)


async def call(server, name: str, **arguments) -> str:  # noqa: ANN001
    result = await server.call_tool(name, arguments)
    return "\n".join(block.text for block in result.content)


async def test_the_four_tools_and_which_only_read(server):
    tools = {t.name: t for t in await server.list_tools()}
    assert set(tools) == {"remember", "recall", "list_disputes", "settle"}
    assert tools["recall"].annotations.read_only_hint
    assert tools["list_disputes"].annotations.read_only_hint
    assert "never on your own judgement" in tools["settle"].description


async def test_recall_marks_a_dispute_and_a_maybe_for_what_they_are(server):
    text = await call(server, "recall", query="what database does atlas use?")
    disputed = text.split("DISPUTED")[1]
    assert "pick none" in disputed and "Atlas uses MongoDB" in disputed
    # The disputed side is not also listed as a plain belief.
    assert "Beliefs:" not in text
    assert "UNDECIDED" in text and "never state it as fact" in text


async def test_settle_without_the_persons_words_is_refused_not_raised(server):
    text = await call(server, "settle", winner_id="m2", said=" ")
    assert text.startswith("Not settled") and "own words" in text


async def test_settle_with_the_persons_words(server):
    text = await call(server, "settle", winner_id="m2", said="Mongo now", loser_ids=["m1"])
    assert text == "Settled: “Atlas uses MongoDB” holds; retired “Atlas uses Postgres”"


async def test_remember_and_list_disputes_read_plainly(server):
    remembered = await call(server, "remember", text="We moved Atlas to MongoDB")
    assert "raised 1 dispute(s) for the person to decide" in remembered
    listed = await call(server, "list_disputes")
    assert listed.startswith("1 open dispute") and "id m2" in listed
