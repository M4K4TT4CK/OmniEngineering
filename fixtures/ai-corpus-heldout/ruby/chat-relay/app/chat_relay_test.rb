# frozen_string_literal: true

require "minitest/autorun"
require "rack/test"
require_relative "chat_relay"

class RelayTest < Minitest::Test
  def setup
    @relay = ChatRelay::Relay.new(clock: -> { Time.utc(2026, 1, 1) })
  end

  # Returns [client, inbox]; inbox collects every frame sent to the client.
  def connect(name = nil)
    inbox = []
    client = @relay.connect { |payload| inbox << JSON.parse(payload) }
    if name
      say(client, type: "hello", name: name)
      inbox.clear
    end
    [client, inbox]
  end

  def say(client, msg)
    @relay.receive(client, JSON.generate(msg))
  end

  def test_hello_welcomes_with_trimmed_name
    client, inbox = connect
    say(client, type: "hello", name: "  alice ")
    assert_equal [{ "type" => "welcome", "name" => "alice" }], inbox
  end

  def test_hello_rejects_bad_names
    client, inbox = connect
    ["", "   ", "x" * 33, "bad\nname", 42, nil].each do |name|
      say(client, type: "hello", name: name)
    end
    assert_equal 6, inbox.size
    assert(inbox.all? { |m| m["type"] == "error" })
    assert_nil client.name
  end

  def test_join_requires_name
    client, inbox = connect
    say(client, type: "join", room: "general")
    assert_equal "error", inbox.last["type"]
    assert_empty @relay.room_stats
  end

  def test_join_rejects_invalid_room_names
    client, inbox = connect("alice")
    ["", "has space", "x" * 65, nil, ["general"]].each do |room|
      say(client, type: "join", room: room)
    end
    assert(inbox.all? { |m| m["type"] == "error" })
    assert_empty @relay.room_stats
  end

  def test_message_relayed_to_others_but_not_sender
    alice, alice_inbox = connect("alice")
    bob, bob_inbox = connect("bob")
    carol, carol_inbox = connect("carol")
    [alice, bob, carol].each { |c| say(c, type: "join", room: "general") }
    [alice_inbox, bob_inbox, carol_inbox].each(&:clear)

    say(alice, type: "message", room: "general", text: "hi all")

    expected = {
      "type" => "message", "room" => "general", "from" => "alice",
      "text" => "hi all", "at" => "2026-01-01T00:00:00.000Z"
    }
    assert_empty alice_inbox
    assert_equal [expected], bob_inbox
    assert_equal [expected], carol_inbox
  end

  def test_messages_stay_in_their_room
    alice, = connect("alice")
    bob, bob_inbox = connect("bob")
    say(alice, type: "join", room: "a")
    say(alice, type: "join", room: "b")
    say(bob, type: "join", room: "b")
    bob_inbox.clear

    say(alice, type: "message", room: "a", text: "only a")
    assert_empty bob_inbox

    say(alice, type: "message", room: "b", text: "only b")
    assert_equal ["only b"], bob_inbox.map { |m| m["text"] }
  end

  def test_must_be_in_room_to_send
    alice, alice_inbox = connect("alice")
    bob, bob_inbox = connect("bob")
    say(bob, type: "join", room: "general")
    bob_inbox.clear

    say(alice, type: "message", room: "general", text: "sneaky")
    assert_equal "error", alice_inbox.last["type"]
    assert_empty bob_inbox
  end

  def test_rejects_empty_or_oversized_text
    alice, inbox = connect("alice")
    say(alice, type: "join", room: "general")
    inbox.clear
    ["", "   ", "x" * 2001, nil, 5].each do |text|
      say(alice, type: "message", room: "general", text: text)
    end
    assert(inbox.all? { |m| m["type"] == "error" })
    assert_equal 5, inbox.size
  end

  def test_join_receives_history_capped_at_fifty
    alice, = connect("alice")
    say(alice, type: "join", room: "general")
    55.times { |i| say(alice, type: "message", room: "general", text: "msg #{i}") }

    bob, bob_inbox = connect("bob")
    say(bob, type: "join", room: "general")

    joined = bob_inbox.last
    assert_equal "joined", joined["type"]
    assert_equal "general", joined["room"]
    assert_equal 50, joined["history"].size
    assert_equal "msg 5", joined["history"].first["text"]
    assert_equal "msg 54", joined["history"].last["text"]
    assert(joined["history"].all? { |m| m["from"] == "alice" })
  end

  def test_room_stats_counts_members
    alice, = connect("alice")
    bob, = connect("bob")
    say(alice, type: "join", room: "general")
    say(bob, type: "join", room: "general")
    say(bob, type: "join", room: "random")
    say(bob, type: "join", room: "random") # joining twice counts once

    assert_equal [{ name: "general", members: 2 }, { name: "random", members: 1 }], @relay.room_stats
  end

  def test_leave_and_disconnect_update_counts
    alice, alice_inbox = connect("alice")
    bob, = connect("bob")
    say(alice, type: "join", room: "general")
    say(bob, type: "join", room: "general")
    say(alice, type: "message", room: "general", text: "keep me")
    say(bob, type: "join", room: "quiet")

    say(alice, type: "leave", room: "general")
    assert_equal({ "type" => "left", "room" => "general" }, alice_inbox.last)
    @relay.disconnect(bob)

    # A room with history survives with zero members; an empty one is dropped.
    assert_equal [{ name: "general", members: 0 }], @relay.room_stats
  end

  def test_leave_unknown_room_is_an_error
    alice, inbox = connect("alice")
    say(alice, type: "leave", room: "general")
    assert_equal "error", inbox.last["type"]
  end

  def test_malformed_input_gets_errors
    client, inbox = connect("alice")
    @relay.receive(client, "not json")
    @relay.receive(client, "[1,2]")
    @relay.receive(client, JSON.generate(type: "dance"))
    @relay.receive(client, [1, 2, 3]) # binary frame
    @relay.receive(client, "x" * (ChatRelay::MAX_FRAME_BYTES + 1))
    assert_equal ["error"] * 5, inbox.map { |m| m["type"] }
  end

  def test_room_cap_evicts_oldest_empty_room
    @relay = ChatRelay::Relay.new(max_rooms: 2)
    alice, = connect("alice")
    say(alice, type: "join", room: "old")
    say(alice, type: "message", room: "old", text: "x")
    say(alice, type: "leave", room: "old")
    say(alice, type: "join", room: "busy")
    say(alice, type: "join", room: "new")

    assert_equal %w[busy new], @relay.room_stats.map { |r| r[:name] }
  end

  def test_room_cap_rejects_when_all_rooms_occupied
    @relay = ChatRelay::Relay.new(max_rooms: 1)
    alice, inbox = connect("alice")
    say(alice, type: "join", room: "one")
    say(alice, type: "join", room: "two")

    assert_equal({ "type" => "error", "error" => "too many rooms" }, inbox.last)
    assert_equal %w[one], @relay.room_stats.map { |r| r[:name] }
  end

  def test_broken_socket_does_not_block_others
    alice, = connect("alice")
    broken = @relay.connect { raise IOError, "closed" }
    say(broken, type: "hello", name: "broken")
    say(broken, type: "join", room: "general")
    bob, bob_inbox = connect("bob")
    [alice, bob].each { |c| say(c, type: "join", room: "general") }
    bob_inbox.clear

    say(alice, type: "message", room: "general", text: "still works")
    assert_equal ["still works"], bob_inbox.map { |m| m["text"] }
  end
end

class AppTest < Minitest::Test
  include Rack::Test::Methods

  def setup
    @relay = ChatRelay::Relay.new
  end

  def app
    ChatRelay::App.new(@relay)
  end

  def test_rooms_endpoint_lists_rooms_and_counts
    alice = @relay.connect { |_| }
    @relay.receive(alice, JSON.generate(type: "hello", name: "alice"))
    @relay.receive(alice, JSON.generate(type: "join", room: "general"))

    get "/rooms"
    assert_equal 200, last_response.status
    assert_equal "application/json", last_response.content_type
    assert_equal({ "rooms" => [{ "name" => "general", "members" => 1 }] }, JSON.parse(last_response.body))
  end

  def test_rooms_endpoint_when_empty
    get "/rooms"
    assert_equal({ "rooms" => [] }, JSON.parse(last_response.body))
  end

  def test_ws_path_without_upgrade
    get "/ws"
    assert_equal 426, last_response.status
  end

  def test_unknown_path
    get "/nope"
    assert_equal 404, last_response.status
  end
end
