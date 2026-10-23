import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.net.ServerSocket;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.net.http.WebSocket;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.CompletionStage;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.TimeUnit;

import static org.junit.jupiter.api.Assertions.*;

class ChatRelayTest {

    private static final ObjectMapper JSON = new ObjectMapper();
    private static final long TIMEOUT_SECONDS = 5;

    private ChatRelay relay;
    private int port;
    private final List<TestClient> opened = new ArrayList<>();

    @BeforeEach
    void startServer() throws IOException {
        port = freePort();
        relay = new ChatRelay();
        relay.start(port);
    }

    @AfterEach
    void stopServer() {
        opened.forEach(TestClient::close);
        relay.stop();
    }

    @Test
    void relaysToOtherMembersOfTheRoomButNotBackToSender() throws Exception {
        TestClient alice = connect("alice");
        join(alice, "general");
        TestClient bob = connect("bob");
        join(bob, "general");

        alice.send(json("type", "message", "room", "general", "text", "hi bob"));
        JsonNode received = bob.next();
        assertEquals("message", received.path("type").asText());
        assertEquals("general", received.path("room").asText());
        assertEquals("alice", received.path("from").asText());
        assertEquals("hi bob", received.path("text").asText());

        // Alice must not get her own message back: her next frame is bob's reply, not an echo.
        bob.send(json("type", "message", "room", "general", "text", "hi alice"));
        assertEquals("hi alice", alice.next().path("text").asText());
    }

    @Test
    void newMembersReceiveTheLast50MessagesAsHistory() throws Exception {
        TestClient alice = connect("alice");
        join(alice, "general");
        TestClient bob = connect("bob");
        join(bob, "general");

        for (int i = 0; i < 55; i++) {
            alice.send(json("type", "message", "room", "general", "text", "m" + i));
        }
        // Bob is a member, so receiving all 55 proves the server has stored them before carol joins.
        for (int i = 0; i < 55; i++) {
            assertEquals("m" + i, bob.next().path("text").asText());
        }

        TestClient carol = connect("carol");
        JsonNode history = join(carol, "general");
        JsonNode messages = history.path("messages");
        assertEquals(50, messages.size());
        assertEquals("m5", messages.get(0).path("text").asText());
        assertEquals("m54", messages.get(49).path("text").asText());
    }

    @Test
    void roomsEndpointListsRoomsWithMemberCounts() throws Exception {
        join(connect("alice"), "general");
        join(connect("bob"), "general");
        join(connect("carol"), "random");

        JsonNode rooms = JSON.readTree(getRooms());
        assertEquals(2, rooms.size());
        assertEquals("general", rooms.get(0).path("name").asText());
        assertEquals(2, rooms.get(0).path("members").asInt());
        assertEquals("random", rooms.get(1).path("name").asText());
        assertEquals(1, rooms.get(1).path("members").asInt());
    }

    @Test
    void memberCountDropsWhenAClientDisconnects() throws Exception {
        join(connect("alice"), "general");
        TestClient bob = connect("bob");
        join(bob, "general");
        assertEquals(2, membersIn("general"));

        bob.close();

        long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(TIMEOUT_SECONDS);
        while (membersIn("general") != 1 && System.nanoTime() < deadline) {
            Thread.sleep(50);
        }
        assertEquals(1, membersIn("general"));
    }

    @Test
    void rejectsMessagesToRoomsTheClientHasNotJoined() throws Exception {
        TestClient alice = connect("alice");
        join(alice, "general");

        alice.send(json("type", "message", "room", "random", "text", "hello?"));
        JsonNode reply = alice.next();
        assertEquals("error", reply.path("type").asText());
    }

    @Test
    void joinRequiresAHelloFirst() throws Exception {
        TestClient client = TestClient.open(port);
        opened.add(client);

        client.send(json("type", "join", "room", "general"));
        assertEquals("error", client.next().path("type").asText());
    }

    /** Connects and completes the hello handshake. */
    private TestClient connect(String name) throws Exception {
        TestClient client = TestClient.open(port);
        opened.add(client);
        client.send(json("type", "hello", "name", name));
        assertEquals("welcome", client.next().path("type").asText());
        return client;
    }

    /** Joins a room and returns the history frame that follows the "joined" frame. */
    private JsonNode join(TestClient client, String room) throws Exception {
        client.send(json("type", "join", "room", room));
        assertEquals("joined", client.next().path("type").asText());
        JsonNode history = client.next();
        assertEquals("history", history.path("type").asText());
        return history;
    }

    private int membersIn(String room) throws Exception {
        for (JsonNode entry : JSON.readTree(getRooms())) {
            if (room.equals(entry.path("name").asText())) {
                return entry.path("members").asInt();
            }
        }
        return 0;
    }

    private String getRooms() throws Exception {
        HttpRequest request = HttpRequest.newBuilder(URI.create("http://localhost:" + port + "/rooms")).build();
        HttpResponse<String> response = HttpClient.newHttpClient().send(request, HttpResponse.BodyHandlers.ofString());
        assertEquals(200, response.statusCode());
        return response.body();
    }

    private static String json(String... pairs) {
        ObjectNode node = JSON.createObjectNode();
        for (int i = 0; i < pairs.length; i += 2) {
            node.put(pairs[i], pairs[i + 1]);
        }
        return node.toString();
    }

    private static int freePort() throws IOException {
        try (ServerSocket socket = new ServerSocket(0)) {
            return socket.getLocalPort();
        }
    }

    /** Minimal WebSocket client that parses each received text frame into a JsonNode. */
    private static final class TestClient implements WebSocket.Listener {
        private final BlockingQueue<JsonNode> inbox = new LinkedBlockingQueue<>();
        private final StringBuilder buffer = new StringBuilder();
        private WebSocket socket;

        static TestClient open(int port) throws Exception {
            TestClient client = new TestClient();
            client.socket = HttpClient.newHttpClient().newWebSocketBuilder()
                .buildAsync(URI.create("ws://localhost:" + port + "/ws"), client)
                .get(TIMEOUT_SECONDS, TimeUnit.SECONDS);
            return client;
        }

        @Override
        public CompletionStage<?> onText(WebSocket ws, CharSequence data, boolean last) {
            buffer.append(data);
            if (last) {
                try {
                    inbox.add(JSON.readTree(buffer.toString()));
                } catch (IOException e) {
                    throw new IllegalStateException("server sent invalid JSON", e);
                } finally {
                    buffer.setLength(0);
                }
            }
            ws.request(1);
            return null;
        }

        void send(String json) throws Exception {
            socket.sendText(json, true).get(TIMEOUT_SECONDS, TimeUnit.SECONDS);
        }

        JsonNode next() throws InterruptedException {
            JsonNode msg = inbox.poll(TIMEOUT_SECONDS, TimeUnit.SECONDS);
            assertNotNull(msg, "timed out waiting for a frame from the server");
            return msg;
        }

        void close() {
            socket.sendClose(WebSocket.NORMAL_CLOSURE, "bye");
        }
    }
}
