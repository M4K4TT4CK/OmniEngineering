# Chat relay

A small WebSocket chat relay built on [aiohttp](https://docs.aiohttp.org/).
Clients connect, pick a display name, and join named rooms. A message sent
to a room goes to every other member of that room. Each room keeps its last
50 messages and sends them to clients when they join.

## Running

```sh
pip install -r requirements.txt
python server.py --host 127.0.0.1 --port 8080
```

## Endpoints

- `GET /ws`: the WebSocket endpoint.
- `GET /rooms`: lists rooms (sorted by name) and their current member counts:
  ```json
  {"rooms": [{"name": "lobby", "members": 2}]}
  ```

## Protocol

Every frame is a JSON object with a `type` field, sent as a text frame.

Client to server:

| type      | fields          | notes                                      |
|-----------|-----------------|--------------------------------------------|
| `hello`   | `name`          | Must come first. Name is 1–32 characters.  |
| `join`    | `room`          | Room name is 1–64 characters.              |
| `leave`   | `room`          |                                            |
| `message` | `room`, `text`  | You must have joined the room. Text is 1–2000 characters. |

Server to client:

| type      | fields                               | when                         |
|-----------|--------------------------------------|------------------------------|
| `welcome` | `name`                               | reply to `hello`             |
| `history` | `room`, `messages` (oldest first)    | reply to `join`              |
| `left`    | `room`                               | reply to `leave`             |
| `message` | `room`, `from`, `text`, `ts`         | someone else posted          |
| `error`   | `error`                              | the request was rejected     |

Example session:

```
> {"type": "hello", "name": "alice"}
< {"type": "welcome", "name": "alice"}
> {"type": "join", "room": "lobby"}
< {"type": "history", "room": "lobby", "messages": []}
> {"type": "message", "room": "lobby", "text": "hi"}
```

Senders do not get their own messages echoed back. A client can be in
several rooms at once, and disconnecting removes it from all of them.

## Design notes

- All state is in memory and is lost when the server restarts.
- Rooms are created on first join. They are not deleted when they empty out,
  so their history stays available. This means the number of rooms grows
  without limit, which you would want to cap before exposing this publicly.
- Display names are not unique and are not authenticated.
- The server runs on a single asyncio event loop, so shared state needs no
  locks. Broadcasts go out concurrently, and a recipient whose connection
  fails during a broadcast does not affect the others.

## Tests

```sh
pytest
```

The tests use `pytest-aiohttp` to start the app in-process and talk to it
over real WebSocket and HTTP connections.
