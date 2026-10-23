# chat-relay

A small WebSocket chat relay in Go. Clients connect, pick a display name, join
named rooms, and send messages that go to everyone else in the room. Each room
keeps its last 50 messages and replays them to anyone who joins.

Its only dependency is [gorilla/websocket](https://github.com/gorilla/websocket).

## Running

```sh
go mod tidy          # fetches gorilla/websocket and writes go.sum
go run . -addr :8080 # -history N changes the per-room history size (default 50)
go test ./...
```

## HTTP endpoints

| Endpoint     | Description                                                       |
|--------------|-------------------------------------------------------------------|
| `GET /ws`    | WebSocket upgrade                                                 |
| `GET /rooms` | `[{"name":"lobby","members":2}, ...]`, sorted by name             |

## WebSocket protocol

Every frame is a JSON text message with a `type` field.

The client sends these:

```json
{"type":"hello","name":"alice"}                      // must be first, within 10 s
{"type":"join","room":"lobby"}
{"type":"message","room":"lobby","text":"hi all"}    // must have joined the room
{"type":"leave","room":"lobby"}
```

The server sends these:

```json
{"type":"welcome","name":"alice"}
{"type":"history","room":"lobby","messages":[{"room":"lobby","from":"bob","text":"...","time":"..."}]}
{"type":"message","room":"lobby","from":"bob","text":"hi","time":"2026-10-23T12:00:00Z"}
{"type":"left","room":"lobby"}
{"type":"error","error":"not in room \"lobby\"; join it first"}
```

- `history` is the reply to `join`, oldest message first. Live messages for
  that room always come after it.
- A sender doesn't get its own messages back.
- A client can be in several rooms at once. Display names don't have to be
  unique.
- A bad request gets an `error` frame and the connection stays open. The one
  exception is a missing or invalid `hello`, which closes the connection with
  code 1008.

## Limits and behaviour

- Names can be up to 32 characters, room names up to 64, and message text up
  to 4000 bytes. Inbound frames can be up to 32 KiB.
- If a client falls 256 frames behind, it is disconnected so it can't hold up
  the room.
- The server pings every 54 s. A connection that sends nothing (not even a
  pong) for 60 s is dropped.
- Cross-origin browser connections are rejected (gorilla's default origin
  check). Non-browser clients are unaffected.
- All state is kept in memory. A room is deleted when its last member leaves,
  unless it has history, in which case it is kept (shown with 0 members) so
  the history can be replayed. The number of rooms is not capped.
