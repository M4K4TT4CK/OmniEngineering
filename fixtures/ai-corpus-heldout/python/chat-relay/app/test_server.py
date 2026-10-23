import asyncio

import pytest
import pytest_asyncio

from server import HISTORY_LIMIT, create_app

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def client(aiohttp_client):
    return await aiohttp_client(create_app())


async def connect(client, name):
    ws = await client.ws_connect("/ws")
    await ws.send_json({"type": "hello", "name": name})
    assert await ws.receive_json() == {"type": "welcome", "name": name}
    return ws


async def join(ws, room):
    await ws.send_json({"type": "join", "room": room})
    reply = await ws.receive_json()
    assert reply["type"] == "history"
    assert reply["room"] == room
    return reply["messages"]


async def assert_nothing_pending(ws):
    # The server answers requests in order, so if the next frame is the
    # reply to this probe, nothing else was queued before it.
    await ws.send_json({"type": "bogus"})
    reply = await ws.receive_json()
    assert reply["type"] == "error"
    assert "unknown message type" in reply["error"]


async def rooms(client):
    resp = await client.get("/rooms")
    assert resp.status == 200
    return {r["name"]: r["members"] for r in (await resp.json())["rooms"]}


async def wait_for_rooms(client, expected):
    # Disconnect cleanup runs in the server's handler after the client
    # closes, so poll briefly rather than asserting immediately.
    for _ in range(50):
        current = await rooms(client)
        if current == expected:
            return
        await asyncio.sleep(0.02)
    assert current == expected


async def test_hello_required_before_anything_else(client):
    ws = await client.ws_connect("/ws")
    await ws.send_json({"type": "join", "room": "lobby"})
    reply = await ws.receive_json()
    assert reply["type"] == "error"
    assert "hello" in reply["error"]
    await ws.close()


async def test_invalid_name_rejected(client):
    ws = await client.ws_connect("/ws")
    for bad in ["", "   ", None, 42, "x" * 33]:
        await ws.send_json({"type": "hello", "name": bad})
        assert (await ws.receive_json())["type"] == "error"
    await ws.close()


async def test_second_hello_rejected(client):
    ws = await connect(client, "alice")
    await ws.send_json({"type": "hello", "name": "bob"})
    assert (await ws.receive_json())["type"] == "error"
    await ws.close()


async def test_malformed_input(client):
    ws = await connect(client, "alice")
    await ws.send_str("not json")
    assert (await ws.receive_json())["error"] == "invalid JSON"
    await ws.send_str("[1, 2]")
    assert (await ws.receive_json())["type"] == "error"
    await ws.send_bytes(b"{}")
    assert (await ws.receive_json())["type"] == "error"
    await ws.close()


async def test_message_relayed_to_others_not_sender(client):
    alice = await connect(client, "alice")
    bob = await connect(client, "bob")
    carol = await connect(client, "carol")
    assert await join(alice, "lobby") == []
    assert await join(bob, "lobby") == []
    await join(carol, "elsewhere")

    await alice.send_json({"type": "message", "room": "lobby", "text": "hi"})
    msg = await bob.receive_json()
    assert msg["type"] == "message"
    assert msg["room"] == "lobby"
    assert msg["from"] == "alice"
    assert msg["text"] == "hi"
    assert isinstance(msg["ts"], float)

    await assert_nothing_pending(alice)
    await assert_nothing_pending(carol)
    for ws in (alice, bob, carol):
        await ws.close()


async def test_cannot_post_to_room_not_joined(client):
    alice = await connect(client, "alice")
    bob = await connect(client, "bob")
    await join(bob, "lobby")
    await alice.send_json({"type": "message", "room": "lobby", "text": "sneaky"})
    assert (await alice.receive_json())["type"] == "error"
    await assert_nothing_pending(bob)
    await alice.close()
    await bob.close()


async def test_invalid_text_rejected(client):
    alice = await connect(client, "alice")
    await join(alice, "lobby")
    for bad in ["", None, 5, "x" * 2001]:
        await alice.send_json({"type": "message", "room": "lobby", "text": bad})
        assert (await alice.receive_json())["type"] == "error"
    await alice.close()


async def test_history_sent_on_join(client):
    alice = await connect(client, "alice")
    await join(alice, "lobby")
    for i in range(3):
        await alice.send_json({"type": "message", "room": "lobby", "text": f"m{i}"})
    await assert_nothing_pending(alice)  # ensures the posts were processed

    bob = await connect(client, "bob")
    history = await join(bob, "lobby")
    assert [m["text"] for m in history] == ["m0", "m1", "m2"]
    assert all(m["from"] == "alice" for m in history)
    await alice.close()
    await bob.close()


async def test_history_capped_at_limit(client):
    alice = await connect(client, "alice")
    await join(alice, "lobby")
    total = HISTORY_LIMIT + 10
    for i in range(total):
        await alice.send_json({"type": "message", "room": "lobby", "text": str(i)})
    await assert_nothing_pending(alice)

    bob = await connect(client, "bob")
    history = await join(bob, "lobby")
    assert [m["text"] for m in history] == [str(i) for i in range(10, total)]
    await alice.close()
    await bob.close()


async def test_history_is_per_room(client):
    alice = await connect(client, "alice")
    await join(alice, "a")
    await join(alice, "b")
    await alice.send_json({"type": "message", "room": "a", "text": "for a"})
    await assert_nothing_pending(alice)

    bob = await connect(client, "bob")
    assert [m["text"] for m in await join(bob, "a")] == ["for a"]
    assert await join(bob, "b") == []
    await alice.close()
    await bob.close()


async def test_join_twice_and_leave(client):
    alice = await connect(client, "alice")
    bob = await connect(client, "bob")
    await join(alice, "lobby")
    await join(bob, "lobby")

    await alice.send_json({"type": "join", "room": "lobby"})
    assert (await alice.receive_json())["type"] == "error"

    await alice.send_json({"type": "leave", "room": "lobby"})
    assert await alice.receive_json() == {"type": "left", "room": "lobby"}
    await alice.send_json({"type": "leave", "room": "lobby"})
    assert (await alice.receive_json())["type"] == "error"

    await bob.send_json({"type": "message", "room": "lobby", "text": "anyone?"})
    await assert_nothing_pending(bob)
    await assert_nothing_pending(alice)
    assert await rooms(client) == {"lobby": 1}
    await alice.close()
    await bob.close()


async def test_rooms_endpoint(client):
    assert await rooms(client) == {}

    alice = await connect(client, "alice")
    bob = await connect(client, "bob")
    await join(alice, "lobby")
    await join(bob, "lobby")
    await join(bob, "games")
    assert await rooms(client) == {"lobby": 2, "games": 1}

    await bob.close()
    await wait_for_rooms(client, {"lobby": 1, "games": 0})
    await alice.close()
    await wait_for_rooms(client, {"lobby": 0, "games": 0})


async def test_rooms_endpoint_sorted(client):
    alice = await connect(client, "alice")
    for name in ["zeta", "alpha", "mid"]:
        await join(alice, name)
    resp = await client.get("/rooms")
    names = [r["name"] for r in (await resp.json())["rooms"]]
    assert names == ["alpha", "mid", "zeta"]
    await alice.close()
