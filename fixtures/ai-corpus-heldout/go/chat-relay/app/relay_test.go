package main

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/gorilla/websocket"
)

// frame is the union of every field the server may send.
type frame struct {
	Type     string    `json:"type"`
	Name     string    `json:"name"`
	Room     string    `json:"room"`
	From     string    `json:"from"`
	Text     string    `json:"text"`
	Error    string    `json:"error"`
	Messages []Message `json:"messages"`
}

func startServer(t *testing.T) *httptest.Server {
	t.Helper()
	srv := httptest.NewServer(NewHub(50).Handler())
	t.Cleanup(srv.Close)
	return srv
}

func dialRaw(t *testing.T, srv *httptest.Server) *websocket.Conn {
	t.Helper()
	url := "ws" + strings.TrimPrefix(srv.URL, "http") + "/ws"
	conn, _, err := websocket.DefaultDialer.Dial(url, nil)
	if err != nil {
		t.Fatalf("dial: %v", err)
	}
	t.Cleanup(func() { conn.Close() })
	return conn
}

// connect dials and completes the hello handshake.
func connect(t *testing.T, srv *httptest.Server, name string) *websocket.Conn {
	t.Helper()
	conn := dialRaw(t, srv)
	send(t, conn, inbound{Type: "hello", Name: name})
	if f := expect(t, conn, "welcome"); f.Name != name {
		t.Fatalf("welcome name = %q, want %q", f.Name, name)
	}
	return conn
}

func send(t *testing.T, conn *websocket.Conn, v any) {
	t.Helper()
	if err := conn.WriteJSON(v); err != nil {
		t.Fatalf("write: %v", err)
	}
}

func recv(t *testing.T, conn *websocket.Conn) frame {
	t.Helper()
	_ = conn.SetReadDeadline(time.Now().Add(2 * time.Second))
	var f frame
	if err := conn.ReadJSON(&f); err != nil {
		t.Fatalf("read: %v", err)
	}
	return f
}

func expect(t *testing.T, conn *websocket.Conn, typ string) frame {
	t.Helper()
	f := recv(t, conn)
	if f.Type != typ {
		t.Fatalf("got frame %+v, want type %q", f, typ)
	}
	return f
}

// join joins a room and waits for the history frame that acknowledges it.
func join(t *testing.T, conn *websocket.Conn, room string) frame {
	t.Helper()
	send(t, conn, inbound{Type: "join", Room: room})
	f := expect(t, conn, "history")
	if f.Room != room {
		t.Fatalf("history for room %q, want %q", f.Room, room)
	}
	return f
}

func say(t *testing.T, conn *websocket.Conn, room, text string) {
	t.Helper()
	send(t, conn, inbound{Type: "message", Room: room, Text: text})
}

func expectMessage(t *testing.T, conn *websocket.Conn, room, from, text string) {
	t.Helper()
	f := expect(t, conn, "message")
	if f.Room != room || f.From != from || f.Text != text {
		t.Fatalf("got message %+v, want room=%q from=%q text=%q", f, room, from, text)
	}
}

func getRooms(t *testing.T, srv *httptest.Server) []RoomInfo {
	t.Helper()
	resp, err := http.Get(srv.URL + "/rooms")
	if err != nil {
		t.Fatalf("GET /rooms: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("GET /rooms: status %d", resp.StatusCode)
	}
	var rooms []RoomInfo
	if err := json.NewDecoder(resp.Body).Decode(&rooms); err != nil {
		t.Fatalf("decode /rooms: %v", err)
	}
	return rooms
}

// Ordering note: frames from one connection are handled in order, and
// delivery into a room happens under a single lock, so "the next frame B
// receives is X" proves B did not receive anything sent before X.

func TestRelaysToOthersButNotSender(t *testing.T) {
	srv := startServer(t)
	alice := connect(t, srv, "alice")
	bob := connect(t, srv, "bob")
	carol := connect(t, srv, "carol")
	for _, c := range []*websocket.Conn{alice, bob, carol} {
		join(t, c, "general")
	}

	say(t, alice, "general", "hi all")
	expectMessage(t, bob, "general", "alice", "hi all")
	expectMessage(t, carol, "general", "alice", "hi all")

	say(t, bob, "general", "hey alice")
	expectMessage(t, alice, "general", "bob", "hey alice") // not her own "hi all"
}

func TestRoomsAreIsolated(t *testing.T) {
	srv := startServer(t)
	alice := connect(t, srv, "alice")
	bob := connect(t, srv, "bob")
	join(t, alice, "a")
	join(t, alice, "b")
	join(t, bob, "b")

	say(t, alice, "a", "only for a")
	say(t, alice, "b", "only for b")
	expectMessage(t, bob, "b", "alice", "only for b")
}

func TestHistoryKeepsLast50AndIsSentOnJoin(t *testing.T) {
	srv := startServer(t)
	alice := connect(t, srv, "alice")
	if f := join(t, alice, "general"); len(f.Messages) != 0 {
		t.Fatalf("new room has %d history messages, want 0", len(f.Messages))
	}
	for i := 0; i < 55; i++ {
		say(t, alice, "general", fmt.Sprintf("msg %d", i))
	}
	// Rejoining returns the history only after all 55 sends were processed.
	join(t, alice, "general")

	bob := connect(t, srv, "bob")
	f := join(t, bob, "general")
	if len(f.Messages) != 50 {
		t.Fatalf("history has %d messages, want 50", len(f.Messages))
	}
	for i, m := range f.Messages {
		want := fmt.Sprintf("msg %d", i+5)
		if m.Text != want || m.From != "alice" || m.Room != "general" || m.Time.IsZero() {
			t.Fatalf("history[%d] = %+v, want text %q from alice", i, m, want)
		}
	}

	// Live messages follow the history.
	say(t, alice, "general", "live")
	expectMessage(t, bob, "general", "alice", "live")
}

func TestLeaveStopsDelivery(t *testing.T) {
	srv := startServer(t)
	alice := connect(t, srv, "alice")
	bob := connect(t, srv, "bob")
	for _, c := range []*websocket.Conn{alice, bob} {
		join(t, c, "r")
		join(t, c, "s")
	}

	send(t, bob, inbound{Type: "leave", Room: "r"})
	if f := expect(t, bob, "left"); f.Room != "r" {
		t.Fatalf("left room %q, want r", f.Room)
	}
	say(t, alice, "r", "bob is gone")
	say(t, alice, "s", "still here")
	expectMessage(t, bob, "s", "alice", "still here")

	say(t, bob, "r", "can I?")
	if f := expect(t, bob, "error"); !strings.Contains(f.Error, "not in room") {
		t.Fatalf("error = %q", f.Error)
	}
}

func TestRoomsEndpoint(t *testing.T) {
	srv := startServer(t)
	if rooms := getRooms(t, srv); len(rooms) != 0 {
		t.Fatalf("rooms = %+v, want none", rooms)
	}

	alice := connect(t, srv, "alice")
	bob := connect(t, srv, "bob")
	join(t, alice, "lobby")
	join(t, bob, "lobby")
	join(t, bob, "games")

	want := []RoomInfo{{"games", 1}, {"lobby", 2}}
	if got := getRooms(t, srv); !reflect.DeepEqual(got, want) {
		t.Fatalf("rooms = %+v, want %+v", got, want)
	}

	// Disconnects are noticed asynchronously, so poll. "games" has no history
	// and no members left, so it disappears.
	bob.Close()
	want = []RoomInfo{{"lobby", 1}}
	deadline := time.Now().Add(2 * time.Second)
	for {
		got := getRooms(t, srv)
		if reflect.DeepEqual(got, want) {
			break
		}
		if time.Now().After(deadline) {
			t.Fatalf("rooms = %+v, want %+v", got, want)
		}
		time.Sleep(10 * time.Millisecond)
	}
}

func TestRoomWithHistoryOutlivesMembers(t *testing.T) {
	srv := startServer(t)
	alice := connect(t, srv, "alice")
	join(t, alice, "notes")
	say(t, alice, "notes", "remember me")
	send(t, alice, inbound{Type: "leave", Room: "notes"})
	expect(t, alice, "left")

	want := []RoomInfo{{"notes", 0}}
	if got := getRooms(t, srv); !reflect.DeepEqual(got, want) {
		t.Fatalf("rooms = %+v, want %+v", got, want)
	}
	bob := connect(t, srv, "bob")
	if f := join(t, bob, "notes"); len(f.Messages) != 1 || f.Messages[0].Text != "remember me" {
		t.Fatalf("history = %+v", f.Messages)
	}
}

func TestHelloRequired(t *testing.T) {
	srv := startServer(t)
	conn := dialRaw(t, srv)
	send(t, conn, inbound{Type: "join", Room: "general"})
	expect(t, conn, "error")

	_ = conn.SetReadDeadline(time.Now().Add(2 * time.Second))
	_, _, err := conn.ReadMessage()
	if !websocket.IsCloseError(err, websocket.ClosePolicyViolation) {
		t.Fatalf("read after failed hello: %v, want policy-violation close", err)
	}
}

func TestInvalidNameRejected(t *testing.T) {
	srv := startServer(t)
	for _, name := range []string{"", "   ", strings.Repeat("x", maxNameLen+1), "bad\nname"} {
		conn := dialRaw(t, srv)
		send(t, conn, inbound{Type: "hello", Name: name})
		if f := expect(t, conn, "error"); f.Error == "" {
			t.Fatalf("name %q: empty error", name)
		}
	}
}

func TestBadRequestsGetErrorsAndConnectionSurvives(t *testing.T) {
	srv := startServer(t)
	alice := connect(t, srv, "alice")

	if err := alice.WriteMessage(websocket.TextMessage, []byte("{not json")); err != nil {
		t.Fatal(err)
	}
	expect(t, alice, "error")

	for _, in := range []inbound{
		{Type: "dance"},
		{Type: "hello", Name: "alice2"},
		{Type: "join", Room: ""},
		{Type: "message", Room: "nowhere", Text: "hi"},
		{Type: "leave", Room: "nowhere"},
	} {
		send(t, alice, in)
		if f := expect(t, alice, "error"); f.Error == "" {
			t.Fatalf("%+v: empty error", in)
		}
	}

	join(t, alice, "general")
	for _, text := range []string{"", "   ", strings.Repeat("x", maxTextLen+1)} {
		say(t, alice, "general", text)
		expect(t, alice, "error")
	}
}
