"""WebSocket chat relay server.

Clients connect to /ws, introduce themselves with a display name, join
named rooms and post messages. Each message is relayed to every other
member of the room, and the last HISTORY_LIMIT messages of each room are
replayed to clients when they join. GET /rooms lists rooms and member counts.
"""

import argparse
import asyncio
import json
import time
from collections import deque

from aiohttp import WSMsgType, web

HISTORY_LIMIT = 50
MAX_NAME_LEN = 32
MAX_ROOM_LEN = 64
MAX_TEXT_LEN = 2000


class Room:
    def __init__(self, name: str) -> None:
        self.name = name
        self.members: set[web.WebSocketResponse] = set()
        self.history: deque[dict] = deque(maxlen=HISTORY_LIMIT)


class Hub:
    """Holds all rooms. Everything runs on one event loop, so no locking."""

    def __init__(self) -> None:
        self.rooms: dict[str, Room] = {}

    def join(self, room_name: str, ws: web.WebSocketResponse) -> Room:
        room = self.rooms.get(room_name)
        if room is None:
            room = self.rooms[room_name] = Room(room_name)
        room.members.add(ws)
        return room

    def leave(self, room_name: str, ws: web.WebSocketResponse) -> None:
        room = self.rooms.get(room_name)
        if room is not None:
            room.members.discard(ws)

    async def post(self, room_name: str, sender: web.WebSocketResponse,
                   name: str, text: str) -> None:
        room = self.rooms[room_name]
        entry = {
            "type": "message",
            "room": room_name,
            "from": name,
            "text": text,
            "ts": time.time(),
        }
        room.history.append(entry)
        recipients = [m for m in room.members if m is not sender]
        await asyncio.gather(*(_safe_send(m, entry) for m in recipients))

    def summary(self) -> list[dict]:
        return [
            {"name": room.name, "members": len(room.members)}
            for room in sorted(self.rooms.values(), key=lambda r: r.name)
        ]


HUB = web.AppKey("hub", Hub)


async def _safe_send(ws: web.WebSocketResponse, payload: dict) -> None:
    # A recipient that disconnected mid-broadcast must not break delivery
    # to the others; its own handler cleans up its memberships.
    try:
        await ws.send_json(payload)
    except (ConnectionError, RuntimeError):
        pass


async def _error(ws: web.WebSocketResponse, message: str) -> None:
    await _safe_send(ws, {"type": "error", "error": message})


def _clean(value: object, max_len: int) -> str | None:
    """Return a stripped non-empty string within max_len, else None."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or len(value) > max_len:
        return None
    return value


async def ws_handler(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)
    hub = request.app[HUB]
    name: str | None = None
    joined: set[str] = set()

    try:
        async for msg in ws:
            if msg.type == WSMsgType.ERROR:
                break
            if msg.type != WSMsgType.TEXT:
                await _error(ws, "only text frames are supported")
                continue
            try:
                data = json.loads(msg.data)
            except ValueError:
                await _error(ws, "invalid JSON")
                continue
            if not isinstance(data, dict):
                await _error(ws, "message must be a JSON object")
                continue

            kind = data.get("type")

            if kind == "hello":
                if name is not None:
                    await _error(ws, "already introduced")
                    continue
                name = _clean(data.get("name"), MAX_NAME_LEN)
                if name is None:
                    await _error(ws, f"name must be 1-{MAX_NAME_LEN} characters")
                    continue
                await ws.send_json({"type": "welcome", "name": name})
                continue

            if name is None:
                await _error(ws, "send hello with a name first")
                continue

            if kind in ("join", "leave", "message"):
                room_name = _clean(data.get("room"), MAX_ROOM_LEN)
                if room_name is None:
                    await _error(ws, f"room must be 1-{MAX_ROOM_LEN} characters")
                    continue
            else:
                await _error(ws, f"unknown message type: {kind!r}")
                continue

            if kind == "join":
                if room_name in joined:
                    await _error(ws, f"already in room {room_name!r}")
                    continue
                room = hub.join(room_name, ws)
                joined.add(room_name)
                await ws.send_json({
                    "type": "history",
                    "room": room_name,
                    "messages": list(room.history),
                })
            elif kind == "leave":
                if room_name not in joined:
                    await _error(ws, f"not in room {room_name!r}")
                    continue
                hub.leave(room_name, ws)
                joined.discard(room_name)
                await ws.send_json({"type": "left", "room": room_name})
            else:  # message
                if room_name not in joined:
                    await _error(ws, f"not in room {room_name!r}")
                    continue
                text = data.get("text")
                if not isinstance(text, str) or not text or len(text) > MAX_TEXT_LEN:
                    await _error(ws, f"text must be 1-{MAX_TEXT_LEN} characters")
                    continue
                await hub.post(room_name, ws, name, text)
    finally:
        for room_name in joined:
            hub.leave(room_name, ws)

    return ws


async def rooms_handler(request: web.Request) -> web.Response:
    return web.json_response({"rooms": request.app[HUB].summary()})


def create_app() -> web.Application:
    app = web.Application()
    app[HUB] = Hub()
    app.router.add_get("/ws", ws_handler)
    app.router.add_get("/rooms", rooms_handler)
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="WebSocket chat relay server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    web.run_app(create_app(), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
