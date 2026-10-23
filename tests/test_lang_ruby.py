"""The Ruby pack beyond the planted corpus: the shapes it must stay silent on.

Every rule's catch is asserted by tests/test_planted_corpus.py. These tests
pin the precision decisions taken after running the pack on rack, sinatra and
railsgoat (ARB-061), so a later "improvement" that re-reports a deliberate
idiom fails here with the idiom named.
"""
from __future__ import annotations

import textwrap

import pytest

from arbiter import ast as ts
from arbiter.engine import run_scan
from arbiter.lang_rules import load_packs
from arbiter.policy import load_config

pytestmark = pytest.mark.skipif(not ts.available(), reason="tree-sitter not installed")


def _rules(tmp_path, code: str, name: str = "lib/app.rb") -> dict[str, list[int]]:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(code), encoding="utf-8")
    (tmp_path / "Gemfile").write_text('source "https://rubygems.org"\n', encoding="utf-8")
    out: dict[str, list[int]] = {}
    for f in run_scan([str(tmp_path)], load_config(None), only=["lang_ruby"],
                      use_adapters=False, use_cache=False).active():
        out.setdefault(f.rule_id.split(".")[-1], []).append(f.location.start_line)
    return out


def test_pack_declares_a_cwe_on_every_rule():
    pack = load_packs()["ruby"]
    missing = [r["id"] for r in pack["rules"] if not r.get("cwe")]
    assert not missing, missing
    assert len(pack["rules"]) >= 18


THREADED = '''
    class Worker
      def start
        @t = Thread.new { run }
      end
    '''


def test_check_then_act_is_silent_outside_threaded_files(tmp_path):
    code = '''
    class Cache
      STORE = {}
      def get(k)
        STORE[k] ||= compute(k)
        STORE[k] = "x" if STORE[k] == "y"
      end
    end
    '''
    assert "check-then-act-unlocked" not in _rules(tmp_path, code)


def test_check_then_act_is_silent_inside_synchronize(tmp_path):
    code = THREADED + '''
      def claim(id)
        @lock.synchronize do
          JOBS[id]["state"] = "running" if JOBS[id]["state"] == "queued"
        end
        @lock.synchronize { @tasks.shift unless @tasks.empty? }
      end
    end
    '''
    assert "check-then-act-unlocked" not in _rules(tmp_path, code)


def test_memoisation_on_an_instance_variable_is_not_a_race(tmp_path):
    # sinatra/base.rb: `@routes[verb] ||= []` at class-definition time;
    # rack/reloader.rb: `@mtimes[file] ||= mtime` under the caller's lock.
    code = THREADED + '''
      def route(verb)
        @routes[verb] ||= []
        @env["sinatra.accept"] ||= parse
      end
    end
    '''
    assert "check-then-act-unlocked" not in _rules(tmp_path, code)


def test_counter_is_reported_only_when_the_file_runs_threads(tmp_path):
    threaded = THREADED + '''
      def run
        @processed += 1
      end
    end
    '''
    single = '''
    class Counter
      def run
        @processed += 1
      end
    end
    '''
    assert _rules(tmp_path, threaded)["unsynchronised-counter"] == [8]
    assert "unsynchronised-counter" not in _rules(tmp_path, single)


def test_worker_loop_with_a_sentinel_or_flag_is_silent(tmp_path):
    code = '''
    class W
      def start
        Thread.new do
          loop do
            job = @q.pop
            break if job == :stop
            run(job)
          end
        end
        Thread.new { loop { tick; break if @stopping } }
        Thread.new do
          until @stopping
            tick
          end
        end
      end
    end
    '''
    assert "endless-worker-loop" not in _rules(tmp_path, code)


def test_worker_loop_killed_elsewhere_in_the_file_is_silent(tmp_path):
    code = '''
    class W
      def start
        @thread = Thread.new { loop { tick } }
      end

      def stop
        @thread.kill
      end
    end
    '''
    assert "endless-worker-loop" not in _rules(tmp_path, code)


def test_http_with_a_timeout_in_scope_is_silent(tmp_path):
    code = '''
    require "net/http"
    class C
      def a(uri)
        Net::HTTP.start(uri.host, uri.port, open_timeout: 2, read_timeout: 5) { |h| h.get("/") }
      end
      def b(uri)
        http = Net::HTTP.new(uri.host, uri.port)
        http.read_timeout = 5
        http.get("/")
      end
      def c(uri)
        Timeout.timeout(5) { Net::HTTP.get(uri) }
      end
      def d
        Faraday.new(url: "http://x", request: { timeout: 5 })
      end
      def e
        HTTParty.get("http://x", timeout: 3)
      end
    end
    '''
    assert "http-no-timeout" not in _rules(tmp_path, code)


def test_http_without_a_timeout_is_reported_per_call(tmp_path):
    code = '''
    require "net/http"
    class C
      def a(uri)
        Net::HTTP.get(uri)
      end
      def b(uri)
        Net::HTTP.start(uri.host, uri.port) { |h| h.get("/") }
      end
    end
    '''
    assert _rules(tmp_path, code)["http-no-timeout"] == [5, 8]


def test_retry_with_counter_and_sleep_is_silent(tmp_path):
    code = '''
    def fetch
      attempts = 0
      begin
        call
      rescue IOError
        attempts += 1
        raise if attempts >= 3
        sleep(0.1 * attempts)
        retry
      end
    end
    '''
    found = _rules(tmp_path, code)
    assert "retry-unbounded" not in found
    assert "retry-without-backoff" not in found


def test_retry_with_backoff_but_no_cap_is_unbounded_only(tmp_path):
    code = '''
    def fetch
      begin
        call
      rescue IOError
        sleep 1
        retry
      end
    end
    '''
    found = _rules(tmp_path, code)
    assert found["retry-unbounded"] == [7]
    assert "retry-without-backoff" not in found


def test_empty_rescue_of_a_named_class_is_a_decision_not_a_swallow(tmp_path):
    # sinatra/base.rb `rescue LoadError`, runner.rb `rescue Errno::ESRCH`,
    # rack/conditional_get.rb `Time.rfc2822(since) rescue nil`.
    code = '''
    begin
      require "oj"
    rescue LoadError
    end

    def kill(pid)
      Process.kill("TERM", pid)
    rescue Errno::ESRCH
    end

    def since(h)
      Time.rfc2822(h) rescue nil
    end

    def generic
      work
    rescue => e
    end
    '''
    assert _rules(tmp_path, code)["swallowed-error"] == [18]


def test_rescue_exception_that_reraises_from_a_block_is_silent(tmp_path):
    code = '''
    def run
      work
    rescue Exception => e
      @scheduler.schedule { raise e }
    end

    def plain
      work
    rescue Exception
      log
    end
    '''
    assert _rules(tmp_path, code)["rescue-exception"] == [10]


def test_sql_with_binds_or_schema_interpolation_is_silent(tmp_path):
    code = '''
    class User
      def self.find_owner(name, id)
        where("name = ?", name)
        where(name: name)
        where("#{table_name}.id = ?", id)
        connection.execute("DELETE FROM #{quoted_table_name} WHERE id = #{connection.quote(id)}")
        order(:created_at)
      end
    end
    '''
    assert "sql-injection" not in _rules(tmp_path, code)


def test_sql_interpolation_and_params_order_are_reported(tmp_path):
    code = '''
    class User
      def self.search(params)
        where("name = '#{params[:name]}'")
        order(params[:sort])
        find_by_sql("SELECT * FROM users WHERE id = #{params[:id]}")
      end
    end
    '''
    assert _rules(tmp_path, code)["sql-injection"] == [4, 5, 6]


def test_shell_multi_arg_escaped_or_integer_interpolation_is_silent(tmp_path):
    code = '''
    require "open3"
    require "shellwords"
    def run(dir, pipe)
      system("ls", dir)
      Open3.capture2("git", "log", dir)
      `ls #{Shellwords.escape(dir)}`
      system "kill -9 #{pipe.pid}"
      system("renice -n 5 -p #{pid.to_i}")
      `git status`
    end
    '''
    assert "command-injection" not in _rules(tmp_path, code)


def test_shell_single_string_interpolation_is_reported(tmp_path):
    code = '''
    require "open3"
    def run(dir)
      `ls #{dir}`
      %x(ls #{dir})
      system("ls #{dir}")
      system("ls #{dir}", exception: true)
      Open3.capture2("ls #{dir}")
      IO.popen("ls #{dir}")
    end
    '''
    assert _rules(tmp_path, code)["command-injection"] == [4, 5, 6, 7, 8, 9]


def test_eval_of_a_file_is_dynamic_eval_not_code_injection(tmp_path):
    # rack/builder.rb: eval(builder_script, binding, path) loads config.ru
    code = '''
    def parse_file(path)
      eval(File.read(path), binding, path)
    end
    def allowed(params)
      eval(params[:expr])
      obj.send(params[:action])
      params[:type].constantize
      obj.send(:safe_method)
      Jobs::Handlers.const_get(:Email)
    end
    '''
    found = _rules(tmp_path, code)
    assert found["dynamic-eval"] == [3, 6]
    assert found["code-injection"] == [6, 7, 8]


def test_safe_load_and_strict_oj_are_silent(tmp_path):
    code = '''
    require "yaml"
    require "oj"
    def cfg(doc)
      YAML.safe_load(doc, permitted_classes: [Date])
      YAML.load(doc, permitted_classes: [Date])
      Oj.load(doc, mode: :strict)
      Oj.strict_load(doc)
      JSON.parse(doc)
    end
    def bad(doc)
      YAML.unsafe_load(doc)
      Oj.load(doc)
    end
    '''
    assert _rules(tmp_path, code)["yaml-unsafe-load"] == [12, 13]


def test_abstract_method_idiom_is_not_a_stub(tmp_path):
    code = '''
    class Worker
      def call
        raise NotImplementedError, "#{self.class} must implement #call"
      end
      def flag
        nil # TODO: wire up
      end
      def ok
        nil
      end
      def pending
        raise NotImplementedError
      end
    end
    '''
    # `call` and `pending` both raise NotImplementedError: the class is an
    # interface (gold benchmark, ARB-065). The TODO stub is still reported.
    assert _rules(tmp_path, code)["stub-method"] == [7]


def test_spec_rules_run_only_on_spec_files(tmp_path):
    code = '''
    RSpec.describe Thing do
      it "works" do
        thing.run
      end
      xit "later" do
        expect(1).to eq(1)
      end
    end
    '''
    assert _rules(tmp_path, code, name="lib/thing.rb") == {}
    found = _rules(tmp_path, code, name="spec/thing_spec.rb")
    assert found["vacuous-spec"] == [3]
    assert found["skipped-spec"] == [6]


def test_spec_delegating_to_a_helper_or_skipping_conditionally_is_silent(tmp_path):
    # sinatra-contrib json_spec.rb: `results_in 'foo' => 'bar'` carries the expect;
    # content_for_spec.rb: `skip` inside `rescue LoadError`.
    code = '''
    RSpec.describe Thing do
      it "encodes" do
        mock_app { get("/") { json 1 } }
        results_in 1
      end
      it "loads" do
        begin
          require "oj"
        rescue LoadError => e
          skip "no oj: #{e.message}"
        end
        expect(1).to eq(1)
      end
      it "needs a tty" do
        skip "no tty" unless $stdout.tty?
        expect(1).to eq(1)
      end
      it { is_expected.to be_ok }
      def results_in(obj)
        expect(body).to eq(obj)
      end
    end
    '''
    assert _rules(tmp_path, code, name="spec/thing_spec.rb") == {}


def test_spec_comparing_without_expect_is_vacuous(tmp_path):
    # sinatra-contrib cookies_spec.rb:143, a real one
    code = '''
    RSpec.describe Cookies do
      it "behaves like Hash#assoc" do
        cookies("foo=bar").assoc("foo") == ["foo", "bar"]
      end
    end
    '''
    assert _rules(tmp_path, code, name="spec/c_spec.rb")["vacuous-spec"] == [3]


def test_tls_off_shapes_and_verify_peer_twin(tmp_path):
    code = '''
    require "openssl"
    def a(http)
      http.verify_mode = OpenSSL::SSL::VERIFY_PEER
      http.verify_mode = OpenSSL::SSL::VERIFY_NONE
      HTTParty.get(url, verify: false, timeout: 2)
      RestClient::Request.execute(url: url, verify_ssl: false, timeout: 2)
      Faraday.new(url, ssl: { verify: true }, request: { timeout: 2 })
    end
    '''
    # Two hits with identical evidence text in one file share a fingerprint and
    # collapse into one finding, so each line here uses a different key.
    assert _rules(tmp_path, code)["tls-verify-off"] == [5, 6, 7]


def test_random_for_a_secret_vs_securerandom(tmp_path):
    code = '''
    require "securerandom"
    def generate_token
      rand(36**12).to_s(36)
    end
    def reset_code
      SecureRandom.hex(8)
    end
    def jitter
      delay = rand(0.5)
      sleep(delay)
    end
    def issue
      session_id = Random.new.bytes(16)
      nonce = SecureRandom.random_number(1 << 32)
    end
    '''
    assert _rules(tmp_path, code)["insecure-random"] == [4, 14]


def test_shell_with_a_tainted_interpolation_beside_an_integer_is_reported(tmp_path):
    # railsgoat models/benefits.rb: the `.to_i` timestamp must not hide `#{name}`.
    code = '''
    def backup(name, path)
      system("cp #{name} #{path}/bak#{Time.now.to_i}")
      `kill #{Process.pid} #{sig}`
    end
    '''
    assert _rules(tmp_path, code)["command-injection"] == [3, 4]


def test_rescue_exception_at_a_rack_call_boundary_is_silent(tmp_path):
    # sinatra/show_exceptions.rb: middleware turning every failure into a 500.
    code = '''
    class ShowErrors
      def call(env)
        @app.call(env)
      rescue Exception => e
        [500, {}, [e.message]]
      end
    end
    '''
    assert "rescue-exception" not in _rules(tmp_path, code)


def test_rescue_exception_outside_a_rack_boundary_is_reported(tmp_path):
    # sinatra-contrib runner.rb:97 `loop { read_nonblock } rescue Exception`,
    # and a service object whose `call` takes no env.
    code = '''
    class Svc
      def log
        loop { @log << pipe.read_nonblock(1) }
      rescue Exception
        @log
      end

      def call
        work
      rescue Exception => e
        logger.error(e)
      end
    end
    '''
    assert _rules(tmp_path, code)["rescue-exception"] == [5, 11]


def test_nil_as_the_value_of_an_assigned_begin_is_a_fallback(tmp_path):
    # rack-protection escaped_params.rb: `post_was = begin ... rescue StandardError; nil end`
    code = '''
    def call(req)
      post_was = begin
        handle(req.POST)
      rescue StandardError
        nil
      end
      empty = begin
        handle(req.GET)
      rescue StandardError
      end
      begin
        handle(req.GET)
      rescue StandardError
        nil
      end
      [post_was, empty]
    end
    '''
    assert _rules(tmp_path, code)["swallowed-error"] == [10, 14]


# --- second precision pass: sidekiq and faraday (ARB-061) -------------------

def test_one_shot_and_guarded_repair_retries_are_silent(tmp_path):
    # sidekiq capsule.rb / client.rb / config.rb, current_attributes.rb,
    # scheduled.rb (NOSCRIPT), web/helpers.rb (re-encode).
    code = '''
    def a
      retryable = true
      begin
        conn.call
      rescue RedisClient::CommandError => ex
        if retryable && ex.message =~ /READONLY/
          conn.close
          retryable = false
          retry
        end
        raise
      end
    end

    def b
      conn.call("EVALSHA", @sha)
    rescue RedisClient::CommandError => e
      raise unless e.message.start_with?("NOSCRIPT")

      @sha = nil
      retry
    end
    '''
    got = _rules(tmp_path, code)
    assert "retry-unbounded" not in got and "retry-without-backoff" not in got


def test_retry_after_a_reconnect_assignment_is_still_reported(tmp_path):
    # `conn = reconnect; retry` is not a one-shot: it hammers forever.
    code = '''
    def a
      begin
        conn.get("/")
      rescue Timeout::Error
        conn = reconnect
        retry
      end
    end
    '''
    got = _rules(tmp_path, code)
    assert got["retry-unbounded"] == [7] and got["retry-without-backoff"] == [7]


def test_rescue_exception_isolating_a_callback_or_handing_off_is_silent(tmp_path):
    # sidekiq cli.rb, config.rb (isolate one callback); processor.rb,
    # job_retry.rb (hand the exception to a supervisor hook).
    code = '''
    def a
      begin
        old_handler.call
      rescue Exception => exc
        puts exc.inspect
      end
      handlers.each do |handler|
        handler.call(ex)
      rescue Exception => e
        logger.error(e)
      end
    end

    def run
      process_one until @done
    rescue Exception => ex
      @callback.call(self, ex)
    end

    def delay(block)
      block&.call(1)
    rescue Exception => e
      handle_exception(e, {context: "retry_in"})
      nil
    end
    '''
    assert "rescue-exception" not in _rules(tmp_path, code)


def test_rescue_exception_that_only_logs_is_still_reported(tmp_path):
    code = '''
    def run
      loop { process_one }
    rescue Exception => e
      logger.error(e)
    end
    '''
    assert _rules(tmp_path, code)["rescue-exception"] == [4]


def test_marshal_deep_copy_is_silent_but_marshal_of_input_is_not(tmp_path):
    # sidekiq redis_connection.rb: Marshal.load(Marshal.dump(options))
    code = '''
    def a(options, raw)
      copy = Marshal.load(Marshal.dump(options))
      Marshal.load(Base64.decode64(raw))
    end
    '''
    assert _rules(tmp_path, code)["marshal-load"] == [4]


def test_expectation_in_a_group_after_hook_covers_its_examples(tmp_path):
    # faraday connection_spec.rb: `after { stubs.verify_stubbed_calls }`
    code = '''
    RSpec.describe "conn" do
      context "with prefix" do
        after { expect(subject.path).to eq("/sake.html") }
        it { conn.path_prefix = "/fish" }
      end

      context "with stubs" do
        after { stubs.verify_stubbed_calls }
        it "overrides params" do
          conn.get("?p=2", p: 3)
        end
      end

      context "with a cleanup hook only" do
        after { conn.close }
        it "gets" do
          conn.get("/")
        end
      end
    end
    '''
    assert _rules(tmp_path, code, "spec/conn_spec.rb")["vacuous-spec"] == [17]


# --- gold benchmark: Rails, RubyGems, Puma (ARB-065) ------------------------

def test_abstract_interfaces_and_unsupported_features_are_not_stubs(tmp_path):
    # ActiveStorage::Service and DatabaseConfig declare many bare raises;
    # StatementPool#dealloc is a private hook; AbstractRenderer#render; and
    # the schema statements say an adapter lacks a feature.
    code = '''
    class Service
      def upload(key, io)
        raise NotImplementedError
      end
      def download(key)
        raise NotImplementedError
      end
      def flag
        nil # TODO: wire up
      end
    end
    class StatementPool
      def clear
        cache.clear
      end
      private
        def dealloc(stmt)
          raise NotImplementedError
        end
    end
    class AbstractRenderer
      def render
        raise NotImplementedError
      end
    end
    class Adapter
      def rename_table(a, b)
        raise NotImplementedError, "rename_table is not implemented"
      end
      def later
        raise NotImplementedError, "not yet implemented"
      end
    end
    class Store
      def purge
        raise NotImplementedError
      end
    end
    '''
    # the TODO beside the interface, "not yet implemented", and the lone stub
    assert _rules(tmp_path, code)["stub-method"] == [10, 32, 37]


def test_bundler_assertion_helpers_and_no_raise_examples_are_not_vacuous(tmp_path):
    # rubygems spec/commands/platform_spec.rb:592, install_spec.rb:781,
    # resolver/platform_spec.rb:115, newgem_options_spec.rb:291
    code = '''
    RSpec.describe "bundle" do
      it "fails when ruby version doesn't match" do
        bundle :update, all: true, raise_on_error: false
        should_be_ruby_version_incorrect
      end
      it "doesn't blow up when the config is empty" do
        FileUtils.touch(bundled_app(".bundle/config"))
        bundle :install
      end
      it "installs" do
        install_gemfile <<-G
          gem "foo"
        G
      end
      it "generates a skeleton" do
        gem_skeleton_assertions
      end
      it "reports the push" do
        mock_confirm_message "Pushed"
        Rake.application["release"].invoke
      end
      it "allows a platform" do
        subject.gem("foo", platform: :ruby)
      end
    end
    '''
    assert _rules(tmp_path, code, "spec/bundle_spec.rb")["vacuous-spec"] == [23]


def test_concurrent_map_env_and_thread_local_memoisation_are_not_races(tmp_path):
    # activerecord sqlite3/quoting.rb:46, notifications/fanout.rb:385, puma server.rb:138
    code = '''
    module Quoting
      QUOTED_COLUMN_NAMES = Concurrent::Map.new
      PLAIN = {}
      module ClassMethods
        def quote_column_name(name)
          QUOTED_COLUMN_NAMES[name] ||= name.to_s.freeze
        end
        def plain(name)
          PLAIN[name] ||= name.to_s
        end
      end
      def boot
        ENV["RACK_ENV"] ||= "development"
        IsolatedExecutionState[:stack] ||= []
        @io.flush unless @io.sync
        @hooks.each(&:call) unless @hooks.empty?
        @remotes.unshift(uri) unless @remotes.include?(uri)
      end
    end
    '''
    assert _rules(tmp_path, code)["check-then-act-unlocked"] == [10, 18]


def test_sql_interpolation_escaped_by_the_driver_is_silent(tmp_path):
    # actioncable subscription_adapter/postgresql.rb:22
    code = '''
    def notify(pg_conn, channel, payload, n)
      pg_conn.exec("NOTIFY #{pg_conn.escape_identifier(channel)}, '#{pg_conn.escape_string(payload)}'")
      pg_conn.exec("LISTEN #{pg_conn.escape_identifier channel}")
      pg_conn.exec("SELECT * FROM t LIMIT #{n.to_i}")
      pg_conn.exec("NOTIFY #{pg_conn.escape_identifier(channel)}, '#{payload}'")
    end
    '''
    assert _rules(tmp_path, code)["sql-injection"] == [6]


def test_rescue_exception_handed_to_an_error_reporter_is_silent(tmp_path):
    # puma response.rb:104 / server.rb:439, fanout.rb:32, bundler worker.rb:72
    code = '''
    class Server
      def handle(env)
        app.call(env)
      rescue Exception => error
        @log_writer.unknown_error error, nil, "Rack app"
      end
      def each_subscriber
        subs.each do |s|
          s.call
        rescue Exception => e
          exceptions << e
        end
      end
      def apply(obj)
        @func.call(obj)
      rescue Exception => e
        WrappedException.new(e)
      end
      def hook
        run_hook
      rescue Exception => e # rubocop:disable Lint/RescueException
        STDERR.puts e.message
      end
      def pool
        work
      rescue Exception => e
        STDERR.puts "Error reached top of thread-pool: #{e.message}"
      end
    end
    '''
    assert _rules(tmp_path, code)["rescue-exception"] == [27]


def test_empty_rescue_with_a_reason_or_a_kept_error_is_not_swallowed(tmp_path):
    # activerecord reflection.rb:460, parallelization/worker.rb:26
    code = '''
    def load(name)
      begin
        compute_class(name)
      rescue
        # Ignored: the constant may not exist yet
      end
      begin
        after_fork
      rescue => @setup_exception; end
      begin
        flush
      rescue StandardError
        # TODO: handle this
      end
      begin
        close
      rescue StandardError
      end
    end
    '''
    assert _rules(tmp_path, code)["swallowed-error"] == [13, 18]


# --- CWE Top 25 round 1 (ARB-062) -------------------------------------------

def test_jwt_decode_without_verification(tmp_path):
    code = '''
    class A
      def a(t)
        JWT.decode(t, nil, false)
      end
      def b(t, k)
        ::JWT.decode(t, k, true, algorithms: ["none"])
      end
      def ok(t, k)
        JWT.decode(t, k, true, algorithm: "HS256")
      end
      def peek_then_verify(t, keys)
        iss = JWT.decode(t, nil, false).first["iss"]
        JWT.decode(t, keys.fetch(iss), true, algorithm: "RS256")
      end
    end
    '''
    assert _rules(tmp_path, code).get("jwt-unverified-decode") == [4, 7]


def test_jwt_literal_key_in_call_or_tied_constant(tmp_path):
    code = '''
    class Tokens
      SIGNING_KEY = "s1gn1ng-k3y-in-source"
      OTHER_KEY = ENV.fetch("OTHER")
      PLACEHOLDER_KEY = "changeme-before-deploy"
      def a(p)
        JWT.encode(p, SIGNING_KEY, "HS256")
      end
      def b(p)
        JWT.encode(p, "an0ther-l1teral-k3y", "HS256")
      end
      def c(p)
        JWT.encode(p, PLACEHOLDER_KEY, "HS256") + JWT.encode(p, OTHER_KEY, "HS256")
      end
    end
    '''
    assert _rules(tmp_path, code).get("jwt-literal-signing-key") == [3, 10]


CSRF_ACTION = '''
      def create
        current_user.update!(params.require(:user).permit(:email))
      end
    end
    '''


def test_csrf_skip_is_reported_in_a_session_controller(tmp_path):
    code = '''
    class ProfilesController < ApplicationController
      skip_before_action :verify_authenticity_token, only: :create
    ''' + CSRF_ACTION
    assert _rules(tmp_path, code, "app/controllers/profiles_controller.rb").get(
        "csrf-protection-disabled") == [3]


@pytest.mark.parametrize("head", [
    # an API controller has no CSRF check to skip
    "class ProfilesController < ActionController::API\n  skip_forgery_protection\n",
    # webhooks authenticate the sender, not a cookie
    "class StripeWebhooksController < ApplicationController\n  skip_forgery_protection\n",
    # the request carries its own credential
    "class ProfilesController < ApplicationController\n  skip_forgery_protection\n"
    "  before_action { authenticate_or_request_with_http_token { |t| ok?(t) } }\n",
    "class ProfilesController < ApplicationController\n  skip_forgery_protection\n"
    "  def verify!\n    OpenSSL::HMAC.hexdigest('SHA256', k, request.raw_post)\n  end\n",
])
def test_csrf_skip_is_fine_where_there_is_no_ambient_cookie_authority(tmp_path, head):
    code = head + CSRF_ACTION
    assert "csrf-protection-disabled" not in _rules(tmp_path, code, "app/controllers/x_controller.rb")


def test_csrf_skip_in_a_get_only_or_abstract_controller_is_fine(tmp_path):
    # railties welcome_controller.rb, pwa_controller.rb; actionmailbox base_controller.rb
    code = '''
    class Rails::PwaController < Rails::ApplicationController
      skip_forgery_protection
      def manifest
        render template: "pwa/manifest", layout: false
      end
    end
    '''
    assert "csrf-protection-disabled" not in _rules(tmp_path, code, "app/controllers/pwa_controller.rb")


def test_null_session_is_a_protection_strategy(tmp_path):
    code = '''
    class ApplicationController < ActionController::Base
      protect_from_forgery with: :null_session
    ''' + CSRF_ACTION
    assert "csrf-protection-disabled" not in _rules(tmp_path, code, "app/controllers/application_controller.rb")


def test_error_detail_to_client(tmp_path):
    code = '''
    class Api < Sinatra::Base
      error do |e|
        halt 500, { error: e.message, trace: e.backtrace }.to_json
      end
      def a
        work
      rescue => e
        render json: { error: e.message }, status: 500
      end
      def b
        work
      rescue ActiveRecord::RecordInvalid => e
        render json: { error: e.message }, status: 422
      end
      def c
        work
      rescue StandardError => err
        logger.error(err.message)
        render json: { error: "failed" }, status: 500
      end
      def d
        work
      rescue Exception => err
        render plain: err.message
      end
    end
    '''
    found = _rules(tmp_path, code)
    assert found.get("error-backtrace-to-client") == [4]
    assert found.get("error-message-to-client") == [9, 25]


def test_csrf_skip_beside_a_signed_token_check_is_fine(tmp_path):
    # activestorage disk_controller.rb: the upload URL carries a signed token
    code = '''
    class ActiveStorage::DiskController < ActiveStorage::BaseController
      skip_forgery_protection
      def update
        if token = decode_verified_token
          upload(token)
        end
      end
    end
    '''
    assert "csrf-protection-disabled" not in _rules(tmp_path, code, "app/controllers/disk_controller.rb")


FORGERY_OFF = '''
Rails.application.configure do
  config.eager_load = true
  config.action_controller.allow_forgery_protection = false
end
'''


@pytest.mark.parametrize("name", ["config/environments/production.rb",
                                  "engines/shop/config/environments/production.rb"])
def test_forgery_protection_off_in_production_is_reported(tmp_path, name):
    assert _rules(tmp_path, FORGERY_OFF, name).get("csrf-protection-off-in-production") == [4]


@pytest.mark.parametrize("name", ["config/environments/test.rb",
                                  "config/environments/development.rb",
                                  "config/application.rb",
                                  "myconfig/environments/production.rb"])
def test_forgery_protection_off_outside_production_is_not_this_rule(tmp_path, name):
    # Rails generates test.rb with exactly this line; only production.rb is read.
    assert "csrf-protection-off-in-production" not in _rules(tmp_path, FORGERY_OFF, name)


def test_forgery_protection_on_in_production_is_silent(tmp_path):
    code = FORGERY_OFF.replace("= false", "= true")
    assert "csrf-protection-off-in-production" not in _rules(
        tmp_path, code, "config/environments/production.rb")


# ------------------------------------------------- CWE Top 25 round 2 (ARB-062)

def test_a_request_value_in_a_path_is_reported_unless_basenamed_or_prefixed(tmp_path):
    code = '''
    class ReportsController < ApplicationController
      def show
        name = params[:name]
        send_file File.join(REPORTS_DIR, name)
      end

      def safe
        path = File.expand_path(File.join(REPORTS_DIR, params[:name]))
        raise "no" unless path.start_with?("#{REPORTS_DIR}/")
        send_file path
      end

      def labels
        [params[:a], params[:b]].join(", ")
      end

      def page
        File.read(File.join(REPORTS_DIR, params[:page].to_i.to_s)) if false
        File.read(params[:file])
      end
    end
    '''
    assert _rules(tmp_path, code, "app/controllers/reports_controller.rb").get(
        "path-traversal-request") == [5, 20]


def test_an_upload_name_is_reported_unless_typed_or_basenamed(tmp_path):
    code = '''
    class UploadsController < ApplicationController
      def create
        upload = params[:file]
        name = upload.original_filename
        File.open(File.join(UPLOAD_DIR, name), "wb") { |f| f.write(upload.read) }
      end

      def checked
        upload = params[:file]
        return head :unsupported_media_type unless %w[.png .jpg].include?(File.extname(upload.original_filename))
        File.binwrite(File.join(UPLOAD_DIR, upload.original_filename), upload.read)
      end

      def attached
        current_user.avatar.attach(io: params[:file], filename: params[:file].original_filename)
      end
    end
    '''
    assert _rules(tmp_path, code, "app/controllers/uploads_controller.rb").get(
        "upload-client-filename") == [6]


def test_ssrf_needs_an_http_client_and_a_caller_chosen_host(tmp_path):
    code = '''
    class PreviewsController < ApplicationController
      def show
        HTTParty.get(params[:url])
        HTTParty.get("https://api.example.com/items/#{params[:id]}")
        Rails.cache.get(params[:key])
        redis.get(params[:key])
      end

      def checked
        uri = URI.parse(params[:url])
        return head :forbidden unless ALLOWED_HOSTS.include?(uri.host)
        Net::HTTP.get(uri)
      end
    end
    '''
    assert _rules(tmp_path, code, "app/controllers/previews_controller.rb").get(
        "ssrf-request-url") == [4]


def test_argument_injection_needs_a_request_value_and_no_double_dash(tmp_path):
    code = '''
    class ImportsController < ApplicationController
      def create
        repo = params[:repo]
        system("git", "clone", "--", repo, "/tmp/x")
        system("echo", repo)
        Open3.capture2("curl", "-sS", repo)
        system("git clone #{repo}")
      end

      def guarded
        host = params[:host]
        return head :bad_request if host.start_with?("-")
        system("ssh", host, "uptime")
      end
    end
    '''
    found = _rules(tmp_path, code, "app/controllers/imports_controller.rb")
    assert found.get("argument-injection") == [7]
    assert found.get("command-injection") == [8]


def test_an_upload_is_reported_once_and_in_sinatra_form(tmp_path):
    # The path built from the name is the finding; the File.open of that
    # path is the same defect and is not reported again.
    code = '''
    def save(file)
      path = Rails.root.join("public", "uploads", file.original_filename)
      File.open(path, "wb") { |f| f.write(file.read) }
    end

    post "/upload" do
      filename = params[:file][:filename]
      File.open("./uploads/#{filename}", "wb") { |f| f.write(params[:file][:tempfile].read) }
    end
    '''
    found = _rules(tmp_path, code)
    assert found.get("upload-client-filename") == [3, 9]
    assert "path-traversal-request" not in found
