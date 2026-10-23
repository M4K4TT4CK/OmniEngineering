# frozen_string_literal: true

require "json"
require "set"
require "time"
require "rack"
require "faye/websocket"

module ChatRelay
  HISTORY_LIMIT = 50
  MAX_ROOMS = 1_000
  MAX_NAME_LENGTH = 32
  MAX_TEXT_LENGTH = 2_000
  MAX_FRAME_BYTES = 16 * 1024
  ROOM_NAME = /\A[A-Za-z0-9_-]{1,64}\z/

  # One connected client. Identity equality on purpose: clients live in Sets
  # while their name and room list change.
  class Client
    attr_accessor :name
    attr_reader :rooms

    def initialize(deliver)
      @deliver = deliver
      @name = nil
      @rooms = Set.new
    end

    def deliver(payload)
      @deliver.call(payload)
    end
  end

  # Transport-independent relay state. All state changes happen under one
  # mutex; outgoing frames are collected and sent after the lock is released
  # so a slow or broken socket never blocks other clients.
  class Relay
    Room = Struct.new(:members, :history)

    def initialize(history_limit: HISTORY_LIMIT, max_rooms: MAX_ROOMS, clock: -> { Time.now.utc })
      @history_limit = history_limit
      @max_rooms = max_rooms
      @clock = clock
      @rooms = {}
      @lock = Mutex.new
    end

    # The block receives each outgoing JSON string for this client.
    def connect(&deliver)
      Client.new(deliver)
    end

    def receive(client, raw)
      flush(@lock.synchronize { dispatch(client, raw) })
    end

    def disconnect(client)
      @lock.synchronize { client.rooms.to_a.each { |name| leave_room(client, name) } }
    end

    def room_stats
      @lock.synchronize do
        @rooms.map { |name, room| { name: name, members: room.members.size } }
              .sort_by { |r| r[:name] }
      end
    end

    private

    def dispatch(client, raw)
      return [error(client, "expected a text frame")] unless raw.is_a?(String)
      return [error(client, "frame too large")] if raw.bytesize > MAX_FRAME_BYTES

      msg = JSON.parse(raw)
      return [error(client, "expected a JSON object")] unless msg.is_a?(Hash)

      case msg["type"]
      when "hello" then hello(client, msg["name"])
      when "join" then join(client, msg["room"])
      when "leave" then leave(client, msg["room"])
      when "message" then message(client, msg["room"], msg["text"])
      else [error(client, "unknown message type")]
      end
    rescue JSON::ParserError, EncodingError
      [error(client, "invalid JSON")]
    end

    def hello(client, name)
      name = name.strip if name.is_a?(String)
      unless name.is_a?(String) && name.length.between?(1, MAX_NAME_LENGTH) && !name.match?(/[[:cntrl:]]/)
        return [error(client, "name must be 1-#{MAX_NAME_LENGTH} printable characters")]
      end

      client.name = name
      [[client, { type: "welcome", name: name }]]
    end

    def join(client, room_name)
      return [error(client, "send hello with a name first")] unless client.name
      return [error(client, "invalid room name")] unless room_name.is_a?(String) && room_name.match?(ROOM_NAME)

      room = @rooms[room_name] || create_room(room_name)
      return [error(client, "too many rooms")] unless room

      room.members << client
      client.rooms << room_name
      [[client, { type: "joined", room: room_name, history: room.history.dup }]]
    end

    def leave(client, room_name)
      return [error(client, "not in that room")] unless client.rooms.include?(room_name)

      leave_room(client, room_name)
      [[client, { type: "left", room: room_name }]]
    end

    def message(client, room_name, text)
      return [error(client, "join the room first")] unless client.rooms.include?(room_name)
      unless text.is_a?(String) && !text.strip.empty? && text.length <= MAX_TEXT_LENGTH
        return [error(client, "text must be 1-#{MAX_TEXT_LENGTH} characters")]
      end

      room = @rooms.fetch(room_name)
      entry = {
        type: "message", room: room_name, from: client.name, text: text,
        at: @clock.call.iso8601(3)
      }.freeze
      room.history << entry
      room.history.shift while room.history.size > @history_limit
      room.members.reject { |m| m.equal?(client) }.map { |m| [m, entry] }
    end

    # Rooms keep their history after everyone leaves, so the room count is
    # capped; at the cap the oldest empty room is evicted to make space.
    def create_room(room_name)
      if @rooms.size >= @max_rooms
        victim, = @rooms.find { |_, room| room.members.empty? }
        return nil unless victim

        @rooms.delete(victim)
      end
      @rooms[room_name] = Room.new(Set.new, [])
    end

    def leave_room(client, room_name)
      client.rooms.delete(room_name)
      room = @rooms[room_name] or return
      room.members.delete(client)
      @rooms.delete(room_name) if room.members.empty? && room.history.empty?
    end

    def error(client, message)
      [client, { type: "error", error: message }]
    end

    def flush(outbox)
      encoded = {}.compare_by_identity
      outbox.each do |client, payload|
        client.deliver(encoded[payload] ||= JSON.generate(payload))
      rescue StandardError
        # A dead socket must not stop delivery to everyone else.
      end
    end
  end

  # Rack app: WebSocket endpoint at /ws, room listing at GET /rooms.
  class App
    def initialize(relay = Relay.new)
      @relay = relay
    end

    def call(env)
      req = Rack::Request.new(env)
      case [req.request_method, req.path_info]
      when ["GET", "/ws"]
        return websocket(env) if Faye::WebSocket.websocket?(env)

        json(426, { error: "WebSocket upgrade required" })
      when ["GET", "/rooms"]
        json(200, { rooms: @relay.room_stats })
      else
        json(404, { error: "not found" })
      end
    end

    private

    def websocket(env)
      ws = Faye::WebSocket.new(env, nil, max_length: MAX_FRAME_BYTES, ping: 30)
      client = @relay.connect { |payload| ws.send(payload) }
      ws.on(:message) { |event| @relay.receive(client, event.data) }
      ws.on(:close) { @relay.disconnect(client) }
      ws.rack_response
    end

    def json(status, body)
      [status, { "content-type" => "application/json" }, [JSON.generate(body)]]
    end
  end
end
