import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import { WebSocket, WebSocketServer } from "ws";

export const HISTORY_LIMIT = 50;
const MAX_NAME_LENGTH = 32;
const MAX_ROOM_NAME_LENGTH = 64;
const MAX_TEXT_LENGTH = 2000;
const MAX_PAYLOAD_BYTES = 16 * 1024;

interface ChatMessage {
  room: string;
  from: string;
  text: string;
  ts: number;
}

interface Client {
  socket: WebSocket;
  name: string | null;
  rooms: Set<string>;
}

interface Room {
  members: Set<Client>;
  history: ChatMessage[];
}

export interface RelayServer {
  httpServer: Server;
  close(): Promise<void>;
}

// Returns the trimmed value if it is a non-empty string of at most `max` characters, otherwise null.
function validText(value: unknown, max: number): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return trimmed.length > 0 && trimmed.length <= max ? trimmed : null;
}

function sendJson(res: ServerResponse, status: number, body: unknown) {
  res.writeHead(status, { "Content-Type": "application/json" });
  res.end(JSON.stringify(body));
}

export function createRelayServer(): RelayServer {
  const rooms = new Map<string, Room>();

  function getRoom(name: string): Room {
    let room = rooms.get(name);
    if (!room) {
      room = { members: new Set(), history: [] };
      rooms.set(name, room);
    }
    return room;
  }

  function send(socket: WebSocket, payload: unknown) {
    if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(payload));
  }

  function sendError(client: Client, message: string) {
    send(client.socket, { type: "error", message });
  }

  function handleHello(client: Client, msg: Record<string, unknown>) {
    const name = validText(msg.name, MAX_NAME_LENGTH);
    if (!name) return sendError(client, `name must be 1-${MAX_NAME_LENGTH} characters`);
    client.name = name;
    send(client.socket, { type: "welcome", name });
  }

  function handleJoin(client: Client, msg: Record<string, unknown>) {
    if (!client.name) return sendError(client, "say hello first");
    const roomName = validText(msg.room, MAX_ROOM_NAME_LENGTH);
    if (!roomName) return sendError(client, `room must be 1-${MAX_ROOM_NAME_LENGTH} characters`);

    const room = getRoom(roomName);
    room.members.add(client);
    client.rooms.add(roomName);
    send(client.socket, {
      type: "joined",
      room: roomName,
      members: [...room.members].map((member) => member.name),
      history: room.history,
    });
  }

  function handleMessage(client: Client, msg: Record<string, unknown>) {
    if (!client.name) return sendError(client, "say hello first");
    const roomName = validText(msg.room, MAX_ROOM_NAME_LENGTH);
    const text = validText(msg.text, MAX_TEXT_LENGTH);
    if (!roomName || !text) return sendError(client, "message needs a room and non-empty text");
    if (!client.rooms.has(roomName)) return sendError(client, `join ${roomName} first`);

    const room = getRoom(roomName);
    const chat: ChatMessage = { room: roomName, from: client.name, text, ts: Date.now() };
    room.history.push(chat);
    if (room.history.length > HISTORY_LIMIT) {
      room.history.splice(0, room.history.length - HISTORY_LIMIT);
    }

    const payload = { type: "message", ...chat };
    for (const member of room.members) {
      if (member !== client) send(member.socket, payload);
    }
  }

  function handleRaw(client: Client, raw: string) {
    let parsed: unknown;
    try {
      parsed = JSON.parse(raw);
    } catch {
      return sendError(client, "invalid JSON");
    }
    if (typeof parsed !== "object" || parsed === null) {
      return sendError(client, "message must be a JSON object");
    }

    const msg = parsed as Record<string, unknown>;
    switch (msg.type) {
      case "hello":
        return handleHello(client, msg);
      case "join":
        return handleJoin(client, msg);
      case "message":
        return handleMessage(client, msg);
      default:
        return sendError(client, `unknown type: ${String(msg.type)}`);
    }
  }

  function handleHttp(req: IncomingMessage, res: ServerResponse) {
    if (req.method === "GET" && req.url === "/rooms") {
      const list = [...rooms]
        .map(([name, room]) => ({ name, members: room.members.size }))
        .sort((a, b) => a.name.localeCompare(b.name));
      return sendJson(res, 200, list);
    }
    return sendJson(res, 404, { error: "not found" });
  }

  const httpServer = createServer(handleHttp);
  const wss = new WebSocketServer({ server: httpServer, maxPayload: MAX_PAYLOAD_BYTES });

  wss.on("connection", (socket) => {
    const client: Client = { socket, name: null, rooms: new Set() };

    socket.on("message", (data) => handleRaw(client, data.toString()));
    socket.on("error", (err) => console.error("socket error:", err.message));
    socket.on("close", () => {
      for (const roomName of client.rooms) rooms.get(roomName)?.members.delete(client);
      client.rooms.clear();
    });
  });

  return {
    httpServer,
    close() {
      return new Promise((resolve, reject) => {
        for (const socket of wss.clients) socket.terminate();
        wss.close();
        httpServer.close((err) => (err ? reject(err) : resolve()));
        httpServer.closeAllConnections();
      });
    },
  };
}

if (require.main === module) {
  const port = Number(process.env.PORT ?? 8080);
  const { httpServer } = createRelayServer();
  httpServer.listen(port, () => console.log(`chat relay listening on port ${port}`));
}
