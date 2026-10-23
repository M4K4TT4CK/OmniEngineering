# Chat relay

A small WebSocket chat relay in Ruby (Rack + [faye-websocket](https://github.com/faye/faye-websocket-ruby) on Puma).
Clients pick a display name, join named rooms, and messages sent to a room are relayed to
everyone else in it. Each room keeps its last 50 messages and sends them to clients as they join.

## Files

- `chat_relay.rb`: relay logic (`ChatRelay::Relay`) and the Rack app (`ChatRelay::App`)
- `config.ru`: Rack entry point
- `chat_relay_test.rb`: Minitest suite
- `Gemfile`

## Running

```sh
bundle install
bundle exec puma -p 9292 config.ru
bundle exec ruby chat_relay_test.rb   # tests
```

## HTTP

`GET /rooms` returns the rooms and how many clients are in each:

```json
{"rooms": [{"name": "general", "members": 2}, {"name": "random", "members": 0}]}
```

## WebSocket protocol

Connect to `ws://localhost:9292/ws`. Every frame is a JSON text frame.

| Client sends | Server replies |
|---|---|
| `{"type":"hello","name":"alice"}` | `{"type":"welcome","name":"alice"}` |
| `{"type":"join","room":"general"}` | `{"type":"joined","room":"general","history":[...]}` |
| `{"type":"message","room":"general","text":"hi"}` | nothing to the sender; others in the room get the message below |
| `{"type":"leave","room":"general"}` | `{"type":"left","room":"general"}` |

Relayed messages (and history entries) look like this:

```json
{"type":"message","room":"general","from":"alice","text":"hi","at":"2026-01-01T12:00:00.000Z"}
```

When a request is invalid, the server sends `{"type":"error","error":"..."}` and keeps the connection open.

Rules:

- You must send `hello` before `join`. Sending `hello` again changes your name. Names are 1–32 printable characters and don't have to be unique.
- Room names are 1–64 characters from `A-Z a-z 0-9 _ -`. A client can be in several rooms.
- You can only send to rooms you've joined. Message text is 1–2000 characters, and frames are capped at 16 KiB.
- Disconnecting removes you from all your rooms.

## Design notes

- `Relay` doesn't depend on the transport. Each client is given a delivery block, which makes the core
  testable without sockets. State is protected by one mutex, and frames are sent after the lock is released,
  so a slow or dead socket can't block anyone else.
- A room that still has history stays after its last member leaves, so a later joiner still sees the backlog.
  A room with no members and no history is dropped. The number of rooms is capped at 1000. When the cap is
  reached, the oldest empty room is evicted, and a new room is refused only if every room is occupied.
- Everything is held in memory in one process. History is lost on restart, and the relay can't be scaled
  horizontally without a shared backend such as Redis pub/sub.
- There's no authentication, and display names are self-declared. Put the server behind TLS (`wss://`) in production.
- The tests cover the relay logic and the HTTP endpoints. The thin faye-websocket glue in `App#websocket`
  isn't exercised over a real socket.
