import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import io.javalin.Javalin;
import io.javalin.websocket.WsContext;

import java.util.ArrayDeque;
import java.util.Deque;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import java.util.concurrent.ConcurrentHashMap;

/**
 * WebSocket chat relay.
 *
 * Client -> server (JSON, one object per frame):
 *   {"type":"hello","name":"alice"}
 *   {"type":"join","room":"general"}
 *   {"type":"message","room":"general","text":"hi"}
 *
 * Server -> client:
 *   {"type":"welcome","name":"alice"}
 *   {"type":"joined","room":"general"}
 *   {"type":"history","room":"general","messages":[...]}   (sent on join)
 *   {"type":"message","room":"general","from":"alice","text":"hi","ts":1700000000000}
 *   {"type":"error","error":"..."}
 *
 * HTTP: GET /rooms -> [{"name":"general","members":2}, ...]
 */
public class ChatRelay {

    static final int HISTORY_LIMIT = 50;

    private static final ObjectMapper JSON = new ObjectMapper();

    private final Map<String, Client> clients = new ConcurrentHashMap<>();
    private final Map<String, Room> rooms = new ConcurrentHashMap<>();
    private Javalin app;

    public static void main(String[] args) {
        int port = args.length > 0 ? Integer.parseInt(args[0]) : 8080;
        new ChatRelay().start(port);
    }

    public synchronized void start(int port) {
        app = Javalin.create()
            .ws("/ws", ws -> {
                ws.onConnect(ctx -> clients.put(ctx.sessionId(), new Client(ctx)));
                ws.onMessage(ctx -> handle(clients.get(ctx.sessionId()), ctx.message()));
                ws.onClose(ctx -> disconnect(ctx.sessionId()));
            })
            .get("/rooms", ctx -> ctx.json(listRooms()))
            .start(port);
    }

    public synchronized void stop() {
        if (app != null) {
            app.stop();
        }
        app = null;
    }

    private void handle(Client client, String raw) {
        if (client == null) {
            return;
        }
        try {
            JsonNode msg = JSON.readTree(raw);
            switch (msg.path("type").asText()) {
                case "hello" -> hello(client, msg.path("name").asText().trim());
                case "join" -> join(client, msg.path("room").asText().trim());
                case "message" -> post(client, msg.path("room").asText(), msg.path("text").asText());
                default -> client.error("unknown message type");
            }
        } catch (JsonProcessingException e) {
            client.error("invalid JSON");
        }
    }

    private void hello(Client client, String name) {
        if (name.isEmpty() || name.length() > 32) {
            client.error("name must be 1-32 characters");
            return;
        }
        client.name = name;
        client.send(msg("welcome").put("name", name));
    }

    private void join(Client client, String roomName) {
        if (client.name == null) {
            client.error("say hello first");
            return;
        }
        if (roomName.isEmpty() || roomName.length() > 64) {
            client.error("room name must be 1-64 characters");
            return;
        }
        if (client.rooms.contains(roomName)) {
            client.error("already in room " + roomName);
            return;
        }
        Room room = rooms.computeIfAbsent(roomName, Room::new);
        synchronized (room) {
            room.members.add(client);
            client.rooms.add(roomName);
            client.send(msg("joined").put("room", roomName));
            ArrayNode history = JSON.createArrayNode();
            room.history.forEach(history::add);
            client.send(msg("history").put("room", roomName).set("messages", history));
        }
    }

    private void post(Client client, String roomName, String text) {
        if (!client.rooms.contains(roomName)) {
            client.error("not in room " + roomName);
            return;
        }
        if (text.isBlank() || text.length() > 2000) {
            client.error("text must be 1-2000 characters");
            return;
        }
        Room room = rooms.get(roomName);
        ObjectNode message = msg("message")
            .put("room", roomName)
            .put("from", client.name)
            .put("text", text)
            .put("ts", System.currentTimeMillis());
        synchronized (room) {
            room.history.addLast(message);
            while (room.history.size() > HISTORY_LIMIT) {
                room.history.removeFirst();
            }
            for (Client member : room.members) {
                if (member != client) {
                    member.send(message);
                }
            }
        }
    }

    private void disconnect(String sessionId) {
        Client client = clients.remove(sessionId);
        if (client == null) {
            return;
        }
        for (String roomName : client.rooms) {
            Room room = rooms.get(roomName);
            synchronized (room) {
                room.members.remove(client);
            }
        }
    }

    private ArrayNode listRooms() {
        ArrayNode list = JSON.createArrayNode();
        new TreeMap<>(rooms).forEach((name, room) -> {
            synchronized (room) {
                list.addObject().put("name", name).put("members", room.members.size());
            }
        });
        return list;
    }

    private static ObjectNode msg(String type) {
        return JSON.createObjectNode().put("type", type);
    }

    private static class Client {
        final WsContext ctx;
        final Set<String> rooms = ConcurrentHashMap.newKeySet();
        volatile String name;

        Client(WsContext ctx) {
            this.ctx = ctx;
        }

        // Jetty does not allow concurrent sends on one session, and broadcasts come from other connections' threads.
        synchronized void send(ObjectNode msg) {
            ctx.send(msg.toString());
        }

        void error(String text) {
            send(msg("error").put("error", text));
        }
    }

    private static class Room {
        final String name;
        final Deque<ObjectNode> history = new ArrayDeque<>();
        final Set<Client> members = ConcurrentHashMap.newKeySet();

        Room(String name) {
            this.name = name;
        }
    }
}
