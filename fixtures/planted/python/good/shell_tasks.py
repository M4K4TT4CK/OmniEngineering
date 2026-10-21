"""Operational tasks the admin API exposes."""
import ast
import importlib
import operator
import os
import shlex
import subprocess

OPS = {"==": operator.eq, "!=": operator.ne, "<": operator.lt, ">": operator.gt}


def archive_logs(directory: str) -> None:
    subprocess.run(["tar", "czf", "logs.tgz", directory], check=True)


def disk_usage(path: str) -> bytes:
    return subprocess.check_output(["du", "-sh", path])


def ping(host: str) -> int:
    return subprocess.call(["ping", "-c", "1", host])


def tail(path: str) -> str:
    return subprocess.run(["tail", "-n", "20", path], capture_output=True, text=True).stdout


def rotate_fixed() -> int:
    # A constant command line is fine through the shell.
    return os.system("logrotate /etc/logrotate.conf")


def grep_logs(pattern: str) -> bytes:
    # Shell needed for the pipe; every variable piece is quoted.
    return subprocess.check_output("grep " + shlex.quote(pattern) + " /var/log/app.log | tail -n 50", shell=True)


def evaluate_filter(expression: str, row: dict) -> bool:
    column, op, literal = expression.split(None, 2)
    return OPS[op](row[column], ast.literal_eval(literal))


def run_hook(name: str) -> None:
    module = importlib.import_module(f"hooks.{name}")
    module.run()


def load_hook_file(path: str) -> dict:
    # Deliberate code loading from an operator-owned file, the way Flask's
    # config.from_pyfile does it: compiled with its real filename for tracebacks.
    namespace: dict = {}
    with open(path, "rb") as fh:
        exec(compile(fh.read(), path, "exec"), namespace)
    return namespace
