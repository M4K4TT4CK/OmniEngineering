"""Operator CLI: pick a job to requeue."""
import sys

import db


def choose_job(conn) -> int:
    rows = db.find_jobs(conn, "failed")
    for i, row in enumerate(rows, 1):
        print(f"{i}. {row[0]}")
    # Blocks on the operator typing, so looping until the input parses is
    # not a busy retry.
    while True:
        try:
            index = int(input("Job number to requeue: "))
            break
        except ValueError:
            print("Not a number, try again.")
    return rows[index - 1][0]


def main() -> int:
    import sqlite3

    conn = sqlite3.connect("jobs.db")
    job_id = choose_job(conn)
    db.rename_job(conn, job_id, "requeued")
    return 0


if __name__ == "__main__":
    sys.exit(main())
