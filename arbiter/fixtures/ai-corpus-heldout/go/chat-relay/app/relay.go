package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"sort"
	"strings"
	"sync"
	"time"
	"unicode"
	"unicode/utf8"

	"github.com/gorilla/websocket"
)

const (
	maxNameLen   = 32       // display name, in characters
	maxRoomLen   = 64       // room name, in characters
	maxTextLen   = 4000     // message text, in bytes
	maxFrameSize = 32 << 10 // inbound WebSocket frame, in bytes (allows for JSON escaping)
	sendBuffer   = 256      // outbound frames queued per client before it is dropped as too slow

	helloWait  = 10 * time.Second
	writeWait  = 10 * time.Second
	pongWait   = 60 * time.Second
	pingPeriod = pongWait * 9 / 10
)

// Message is a chat message as stored in room history and relayed to clients.
type Message struct {
	Room string    `json:"room"`
	From string    `json:"from"`
	Text string    `json:"text"`
	Time time.Time `json:"time"`
}

// RoomInfo is one entry of the GET /rooms response.
type RoomInfo struct {
	Name    string `json:"name"`
	Members int    `json:"members"`
}

// inbound is any frame a client may send.
type inbound struct {
	Type string `json:"type"`
	Name string `json:"name,omitempty"`
	Room string `json:"room,omitempty"`
	Text string `json:"text,omitempty"`
}

// Outbound frames.
type (
	welcomeFrame struct {
		Type string `json:"type"`
		Name string `json:"name"`
	}
	historyFrame struct {
		Type     string    `json:"type"`
		Room     string    `json:"room"`
		Messages []Message `json:"messages"`
	}
	leftFrame struct {
		Type string `json:"type"`
		Room string `json:"room"`
	}
	messageFrame struct {
		Type string `json:"type"`
		Message
	}
	errorFrame struct {
		Type  string `json:"type"`
		Error string `json:"error"`
	}
)

// Hub owns all rooms and their members. A single mutex guards everything; the
// work done under it is small (map updates and non-blocking channel sends).
type Hub struct {
	historySize int
	upgrader    websocket.Upgrader

	mu    sync.Mutex
	rooms map[string]*room
}

type room struct {
	members map[*client]struct{}
	history []Message // oldest first, at most Hub.historySize entries
}

type client struct {
	name  string
	send  chan []byte
	done  chan struct{}
	rooms map[string]struct{} // guarded by Hub.mu

	closeOnce   sync.Once
	closeCode   int
	closeReason string
}

// NewHub returns a hub that keeps the last historySize messages of each room.
func NewHub(historySize int) *Hub {
	if historySize < 0 {
		historySize = 0
	}
	return &Hub{historySize: historySize, rooms: make(map[string]*room)}
}

// Handler returns the HTTP routes: GET /ws (WebSocket) and GET /rooms (JSON).
func (h *Hub) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /ws", h.serveWS)
	mux.HandleFunc("GET /rooms", h.serveRooms)
	return mux
}

// Rooms lists every room with its current member count, sorted by name.
func (h *Hub) Rooms() []RoomInfo {
	h.mu.Lock()
	defer h.mu.Unlock()
	out := make([]RoomInfo, 0, len(h.rooms))
	for name, r := range h.rooms {
		out = append(out, RoomInfo{Name: name, Members: len(r.members)})
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Name < out[j].Name })
	return out
}

func (h *Hub) serveRooms(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(h.Rooms())
}

func (h *Hub) serveWS(w http.ResponseWriter, r *http.Request) {
	conn, err := h.upgrader.Upgrade(w, r, nil)
	if err != nil {
		return // Upgrade has already replied with an HTTP error.
	}
	defer conn.Close()
	conn.SetReadLimit(maxFrameSize)

	name, err := readHello(conn)
	if err != nil {
		// No writer goroutine exists yet, so writing directly is safe.
		deadline := time.Now().Add(writeWait)
		_ = conn.SetWriteDeadline(deadline)
		_ = conn.WriteMessage(websocket.TextMessage, mustMarshal(errorFrame{"error", err.Error()}))
		_ = conn.WriteControl(websocket.CloseMessage,
			websocket.FormatCloseMessage(websocket.ClosePolicyViolation, "hello required"), deadline)
		return
	}

	c := &client{
		name:  name,
		send:  make(chan []byte, sendBuffer),
		done:  make(chan struct{}),
		rooms: make(map[string]struct{}),
	}
	writerDone := make(chan struct{})
	go func() {
		c.writeLoop(conn)
		close(writerDone)
	}()
	c.enqueue(mustMarshal(welcomeFrame{"welcome", name}))

	_ = conn.SetReadDeadline(time.Now().Add(pongWait))
	conn.SetPongHandler(func(string) error {
		return conn.SetReadDeadline(time.Now().Add(pongWait))
	})
	h.readLoop(c, conn)

	h.removeClient(c)
	c.close(websocket.CloseNormalClosure, "")
	<-writerDone
}

func readHello(conn *websocket.Conn) (string, error) {
	_ = conn.SetReadDeadline(time.Now().Add(helloWait))
	_, data, err := conn.ReadMessage()
	if err != nil {
		return "", err
	}
	var in inbound
	if err := json.Unmarshal(data, &in); err != nil || in.Type != "hello" {
		return "", errors.New(`first message must be {"type":"hello","name":"..."}`)
	}
	return cleanName(in.Name, maxNameLen, "name")
}

// readLoop handles client frames until the connection fails or is closed.
func (h *Hub) readLoop(c *client, conn *websocket.Conn) {
	for {
		_, data, err := conn.ReadMessage()
		if err != nil {
			return
		}
		_ = conn.SetReadDeadline(time.Now().Add(pongWait))
		var in inbound
		if err := json.Unmarshal(data, &in); err != nil {
			c.sendError("invalid JSON")
			continue
		}
		if err := h.handle(c, in); err != nil {
			c.sendError(err.Error())
		}
	}
}

func (h *Hub) handle(c *client, in inbound) error {
	switch in.Type {
	case "join", "leave", "message":
	case "hello":
		return errors.New("already said hello")
	default:
		return fmt.Errorf("unknown message type %q", in.Type)
	}

	roomName, err := cleanName(in.Room, maxRoomLen, "room")
	if err != nil {
		return err
	}
	switch in.Type {
	case "join":
		h.join(c, roomName)
		return nil
	case "leave":
		return h.leave(c, roomName)
	default:
		if strings.TrimSpace(in.Text) == "" {
			return errors.New("text is required")
		}
		if len(in.Text) > maxTextLen {
			return fmt.Errorf("text must be at most %d bytes", maxTextLen)
		}
		return h.publish(c, roomName, in.Text)
	}
}

// join adds c to the room (creating it if needed) and sends the room history.
// Both happen under the lock, so no live message can overtake the history.
// Joining a room the client is already in just resends the history.
func (h *Hub) join(c *client, name string) {
	h.mu.Lock()
	defer h.mu.Unlock()
	r := h.rooms[name]
	if r == nil {
		r = &room{members: make(map[*client]struct{}), history: make([]Message, 0, h.historySize)}
		h.rooms[name] = r
	}
	r.members[c] = struct{}{}
	c.rooms[name] = struct{}{}
	c.enqueue(mustMarshal(historyFrame{Type: "history", Room: name, Messages: r.history}))
}

func (h *Hub) leave(c *client, name string) error {
	h.mu.Lock()
	defer h.mu.Unlock()
	if _, ok := c.rooms[name]; !ok {
		return fmt.Errorf("not in room %q", name)
	}
	h.removeFromRoomLocked(c, name)
	c.enqueue(mustMarshal(leftFrame{"left", name}))
	return nil
}

func (h *Hub) publish(c *client, name, text string) error {
	h.mu.Lock()
	defer h.mu.Unlock()
	if _, ok := c.rooms[name]; !ok {
		return fmt.Errorf("not in room %q; join it first", name)
	}
	r := h.rooms[name]
	m := Message{Room: name, From: c.name, Text: text, Time: time.Now().UTC()}
	r.history = append(r.history, m)
	if over := len(r.history) - h.historySize; over > 0 {
		r.history = r.history[over:]
	}
	data := mustMarshal(messageFrame{Type: "message", Message: m})
	for member := range r.members {
		if member != c {
			member.enqueue(data)
		}
	}
	return nil
}

func (h *Hub) removeClient(c *client) {
	h.mu.Lock()
	defer h.mu.Unlock()
	for name := range c.rooms {
		h.removeFromRoomLocked(c, name)
	}
}

// removeFromRoomLocked drops c from a room. A room that ends up with neither
// members nor history is deleted; rooms with history are kept so it can be
// replayed to the next joiner.
func (h *Hub) removeFromRoomLocked(c *client, name string) {
	delete(c.rooms, name)
	r := h.rooms[name]
	if r == nil {
		return
	}
	delete(r.members, c)
	if len(r.members) == 0 && len(r.history) == 0 {
		delete(h.rooms, name)
	}
}

// enqueue queues a frame without blocking. A client whose buffer is full is
// disconnected rather than allowed to stall the rest of the room.
func (c *client) enqueue(data []byte) {
	select {
	case c.send <- data:
	default:
		c.close(websocket.ClosePolicyViolation, "client too slow")
	}
}

func (c *client) sendError(msg string) {
	c.enqueue(mustMarshal(errorFrame{"error", msg}))
}

func (c *client) close(code int, reason string) {
	c.closeOnce.Do(func() {
		c.closeCode, c.closeReason = code, reason
		close(c.done)
	})
}

// writeLoop is the only goroutine that writes data frames to conn. Closing
// conn on exit also unblocks the reader.
func (c *client) writeLoop(conn *websocket.Conn) {
	ticker := time.NewTicker(pingPeriod)
	defer func() {
		ticker.Stop()
		conn.Close()
	}()
	for {
		select {
		case data := <-c.send:
			_ = conn.SetWriteDeadline(time.Now().Add(writeWait))
			if err := conn.WriteMessage(websocket.TextMessage, data); err != nil {
				return
			}
		case <-ticker.C:
			if err := conn.WriteControl(websocket.PingMessage, nil, time.Now().Add(writeWait)); err != nil {
				return
			}
		case <-c.done:
			_ = conn.WriteControl(websocket.CloseMessage,
				websocket.FormatCloseMessage(c.closeCode, c.closeReason), time.Now().Add(writeWait))
			return
		}
	}
}

func cleanName(s string, max int, what string) (string, error) {
	s = strings.TrimSpace(s)
	switch {
	case s == "":
		return "", fmt.Errorf("%s is required", what)
	case utf8.RuneCountInString(s) > max:
		return "", fmt.Errorf("%s must be at most %d characters", what, max)
	case strings.IndexFunc(s, unicode.IsControl) >= 0:
		return "", fmt.Errorf("%s must not contain control characters", what)
	}
	return s, nil
}

func mustMarshal(v any) []byte {
	data, err := json.Marshal(v)
	if err != nil {
		panic(err) // only fixed, known-good types are marshalled
	}
	return data
}
