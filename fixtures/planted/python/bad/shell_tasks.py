"""Operational tasks the admin API exposes."""
import os
import subprocess


def archive_logs(directory: str) -> None:
    subprocess.run(f"tar czf logs.tgz {directory}", shell=True, check=True)  # expect: arbiter/python.command-injection


def disk_usage(path: str) -> bytes:
    return subprocess.check_output("du -sh " + path, shell=True)  # expect: arbiter/python.command-injection


def ping(host: str) -> int:
    return os.system("ping -c 1 " + host)  # expect: arbiter/python.command-injection


def tail(path: str) -> str:
    return os.popen(f"tail -n 20 {path}").read()  # expect: arbiter/python.command-injection


def evaluate_filter(expression: str, row: dict) -> bool:
    return eval(expression, {"row": row})  # expect: arbiter/python.code-injection


def run_hook(source: str) -> None:
    exec(source)  # expect: arbiter/python.code-injection
