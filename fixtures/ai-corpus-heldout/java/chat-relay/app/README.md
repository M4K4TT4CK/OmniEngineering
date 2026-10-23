# Chat relay

A small WebSocket chat server in Java. Clients pick a display name, join named rooms, and send messages that are relayed to the other members of the room. Each room keeps its last 50 messages and replays them to anyone who joins.

Built on [Javalin](https://javalin.io) 6 (Jetty underneath) and Jackson. Requires Java 17+.

## Files

- `ChatRelay.java` – the server
- `ChatRelayTest.java` – JUnit 5 tests that start the server on a free port and talk to it with `java.net.http.WebSocket`
- `build.gradle` – build, dependencies, and the `run`/`test` tasks

## Running

```
gradle run          # listens on port 8080
gradle run --args=9000   # or pick a port
gradle test
```

## Protocol

Connect to `ws://localhost:8080/ws`. Every frame is a JSON object with a `type`.

Client to server:

| Frame | Meaning |
|---|---|
| `{"type":"hello","name":"alice"}` | Set display name (1–32 chars). Required before joining. |
| `{"type":"join","room":"general"}` | Join a room (1–64 chars). Rooms are created on first join. |
| `{"type":"message","room":"general","text":"hi"}` | Send to a room you have joined (1–2000 chars). |

Server to client:

| Frame | Meaning |
|---|---|
| `{"type":"welcome","name":"alice"}` | Name accepted. |
| `{"type":"joined","room":"general"}` | Room joined. |
| `{"type":"history","room":"general","messages":[...]}` | Last up to 50 messages, sent right after `joined`. |
| `{"type":"message","room":"general","from":"alice","text":"hi","ts":1700000000000}` | A relayed message. Senders do not get their own messages back. |
| `{"type":"error","error":"..."}` | The last frame was rejected. |

## HTTP

`GET /rooms` returns every room the server knows about with its current member count:

```json
[{"name":"general","members":2},{"name":"random","members":0}]
```

Rooms are kept in memory only; a restart clears them.
