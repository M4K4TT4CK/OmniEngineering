# chat-relay

A small WebSocket chat relay in TypeScript (Node.js). Clients pick a display name, join named rooms, and send messages that are relayed to everyone else in the room. Each room keeps its last 50 messages, which are sent to a client when it joins. An HTTP endpoint lists rooms and their member counts.

Libraries: [`ws`](https://github.com/websockets/ws) for WebSockets, Node's built-in `http` for the HTTP endpoint, and `node:test` for tests.

## Files

- `server.ts`: the relay and HTTP server.
- `server.test.ts`: automated tests.
- `package.json`, `tsconfig.json`: project configuration.

## Setup

```sh
npm install
npm run build
npm start          # listens on $PORT, default 8080
```

Development: `npx tsx server.ts`.

## Protocol

All WebSocket frames are JSON text. Connect to `ws://host:8080`.

Client to server:

| Message | Fields | Notes |
| --- | --- | --- |
| `{"type":"hello","name":"alice"}` | `name`: 1–32 characters | Must be sent before joining or messaging. Can be sent again to rename. |
| `{"type":"join","room":"general"}` | `room`: 1–64 characters | Creates the room if needed. |
| `{"type":"message","room":"general","text":"hi"}` | `room`, `text`: 1–2000 characters | The client must have joined the room. |

Server to client:

| Message | Sent when |
| --- | --- |
| `{"type":"welcome","name":"alice"}` | After a valid `hello`. |
| `{"type":"joined","room":"general","members":["alice","bob"],"history":[...]}` | After a valid `join`. `history` holds up to the last 50 messages, oldest first. |
| `{"type":"message","room":"general","from":"alice","text":"hi","ts":1700000000000}` | Another member of the room sent a message. The sender does not get an echo. |
| `{"type":"error","message":"..."}` | The request was invalid. The connection stays open. |

Each history entry has the same shape as a `message` event, minus the `type` field.

## HTTP

`GET /rooms` returns a JSON array sorted by name:

```json
[
  { "name": "general", "members": 2 },
  { "name": "random", "members": 0 }
]
```

Rooms are kept for the life of the process, so a room with no members still appears with `"members": 0`. Any other path returns 404.

## Notes and limits

- State is in memory. Restarting the server clears all rooms and history.
- A client that disconnects is removed from every room it joined.
- Incoming frames are capped at 16 KiB.

## Tests

```sh
npm test
```

The tests start the server on a random port and exercise relaying, room isolation, history limits, input validation, and the `/rooms` endpoint.
