"""App-level defects found in AI-written services (ARB-083).

The planted corpus cannot hold an application with no authentication at all
(every planted repository has auth code), so the repository-level cases are
tested here on small apps written to a temporary directory. Each defect has a
twin that must stay silent.
"""
from __future__ import annotations

from arbiter.engine import run_scan
from arbiter.policy import load_config

DESTRUCTIVE = "arbiter/aiapp.unauthenticated-destructive-route"
RELAY = "arbiter/aiapp.open-mail-relay"
STORE = "arbiter/aiapp.store-never-evicted"


def _findings(tmp_path, files: dict[str, str]) -> list[tuple[str, str, int]]:
    for name, source in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(source, encoding="utf-8")
    rep = run_scan([str(tmp_path)], load_config(None), only=["aiapp"], use_adapters=False)
    return sorted((f.rule_id, f.location.path, f.location.start_line) for f in rep.active())


FLASK_NO_AUTH = '''from flask import Flask

app = Flask(__name__)


@app.get("/files")
def list_files():
    return []


@app.delete("/files/<name>")
def delete_file(name):
    return "", 204
'''


def test_delete_route_in_an_app_with_no_auth_is_reported(tmp_path):
    assert _findings(tmp_path, {"app.py": FLASK_NO_AUTH}) == [(DESTRUCTIVE, "app.py", 11)]


def test_the_same_app_with_a_login_decorator_is_silent(tmp_path):
    src = FLASK_NO_AUTH.replace('@app.delete("/files/<name>")\n',
                                '@app.delete("/files/<name>")\n@login_required\n')
    assert _findings(tmp_path, {"app.py": src}) == []


def test_auth_in_another_file_counts_for_the_app(tmp_path):
    # A Sinatra app whose auth lives in a helper file: the route file shows no
    # guard, and Ruby routes are guarded by `before` filters, so it is silent.
    files = {
        "app.rb": 'require "sinatra"\n\ndelete "/files/:name" do\n  204\nend\n',
        "auth.rb": "def authenticate!\n  halt 401 unless request.env['HTTP_AUTHORIZATION']\nend\n",
    }
    assert _findings(tmp_path, files) == []


def test_go_route_without_the_wrapper_its_siblings_use_is_reported(tmp_path):
    src = '''package main

import "net/http"

func routes(mux *http.ServeMux, a *api) {
	mux.HandleFunc("GET /notes", a.authed(a.list))
	mux.HandleFunc("DELETE /notes/{id}", a.remove)
}
'''
    assert _findings(tmp_path, {"main.go": src}) == [(DESTRUCTIVE, "main.go", 7)]
    assert _findings(tmp_path, {"main.go": src.replace("a.remove)", "a.authed(a.remove))")}) == []


def test_admin_read_routes_are_not_destructive(tmp_path):
    src = "const app = express();\napp.get('/admin/version', (req, res) => res.json({}));\n"
    assert _findings(tmp_path, {"server.js": src}) == []


MAILER = '''import smtplib
from fastapi import FastAPI

app = FastAPI()


@app.post("/jobs")
def submit(job: dict):
    with smtplib.SMTP("localhost") as s:
        s.sendmail("app@example.com", [job["to"]], job["body"])
    return {}
'''


def test_unauthenticated_write_route_in_a_mail_sending_app_is_a_relay(tmp_path):
    assert _findings(tmp_path, {"api.py": MAILER}) == [(RELAY, "api.py", 7)]


def test_the_relay_is_silent_once_the_app_authenticates(tmp_path):
    src = MAILER.replace("def submit(job: dict):", "def submit(job: dict, user=Depends(get_current_user)):")
    assert _findings(tmp_path, {"api.py": src}) == []


def test_outbound_smtp_credentials_are_not_inbound_auth(tmp_path):
    # smtp.PlainAuth / PasswordAuthentication authenticate the service to
    # its mail server, not callers to the service.
    src = MAILER.replace('smtplib.SMTP("localhost") as s:',
                         'smtplib.SMTP("localhost") as s:\n        s.login("user", "PasswordAuthentication")')
    assert [r for r, _, _ in _findings(tmp_path, {"api.py": src})] == [RELAY]


STORE_TS = '''import { randomUUID } from "node:crypto";

export class Queue {
  private readonly jobs = new Map<string, { id: string }>();

  submit() {
    const job = { id: randomUUID() };
    this.jobs.set(job.id, job);
    return job;
  }
}
'''


def test_a_map_of_fresh_ids_that_is_never_emptied_is_reported(tmp_path):
    assert _findings(tmp_path, {"queue.ts": STORE_TS}) == [(STORE, "queue.ts", 8)]


def test_eviction_elsewhere_in_the_file_silences_it(tmp_path):
    src = STORE_TS.replace("    return job;\n  }\n",
                           "    return job;\n  }\n\n  done(id: string) {\n    this.jobs.delete(id);\n  }\n")
    assert _findings(tmp_path, {"queue.ts": src}) == []


def test_a_rollback_in_the_inserting_function_is_not_eviction(tmp_path):
    src = '''package q

import "crypto/rand"

type Job struct{ ID string }

type Queue struct {
	jobs map[string]*Job
	ch   chan string
}

func (q *Queue) Submit() error {
	var b [8]byte
	rand.Read(b[:])
	job := &Job{ID: string(b[:])}
	q.jobs[job.ID] = job
	select {
	case q.ch <- job.ID:
		return nil
	default:
		delete(q.jobs, job.ID)
		return nil
	}
}
'''
    assert _findings(tmp_path, {"queue.go": src}) == [(STORE, "queue.go", 16)]


def test_a_bounded_key_space_is_not_a_store_that_grows(tmp_path):
    # Keyed by a caller's parameter, not by the id of a newly created item:
    # Zulip's import SubscriberHandler maps stream ids it is given.
    src = '''import uuid


class Handler:
    def __init__(self):
        self.stream_info = {}

    def set_info(self, users, stream_id):
        self.stream_info[stream_id] = users
'''
    assert _findings(tmp_path, {"h.py": src}) == []
