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
    class Base
      def call
        raise NotImplementedError, "#{self.class} must implement #call"
      end
      def pending
        raise NotImplementedError
      end
      def flag
        nil # TODO: wire up
      end
      def ok
        nil
      end
    end
    '''
    assert _rules(tmp_path, code)["stub-method"] == [7, 10]


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
