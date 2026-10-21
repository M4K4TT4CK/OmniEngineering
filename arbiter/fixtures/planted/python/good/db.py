"""Job persistence on SQLite, with a SQLAlchemy engine for reporting."""
import logging
import sqlite3

from sqlalchemy import create_engine, text

log = logging.getLogger(__name__)
engine = create_engine("sqlite:///jobs.db")

COLUMNS = ("id", "status", "name", "owner")


def get_job(conn: sqlite3.Connection, job_id: str):
    cur = conn.cursor()
    cur.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
    return cur.fetchone()


def find_jobs(conn: sqlite3.Connection, status: str):
    log.debug("looking up jobs with status %s", status)
    return conn.execute("SELECT id, status FROM jobs WHERE status = %s", (status,)).fetchall()


def rename_job(conn: sqlite3.Connection, job_id: int, name: str) -> None:
    conn.execute("UPDATE jobs SET name = :name WHERE id = :id", {"name": name, "id": job_id})
    conn.commit()


def delete_job(conn: sqlite3.Connection, job_id: int) -> None:
    conn.execute("DELETE FROM jobs " + "WHERE id = ?", (job_id,))
    conn.commit()
    log.info(f"deleted job {job_id}")


def count_by_owner(owner: str) -> int:
    with engine.connect() as conn:
        return conn.execute(text("SELECT count(*) FROM jobs WHERE owner = :owner"), {"owner": owner}).scalar()


def search(conn: sqlite3.Connection, term: str):
    stmt = (
        "SELECT id FROM jobs "
        "WHERE name LIKE ?"
    )
    return conn.execute(stmt, (f"%{term}%",)).fetchall()


def describe(row) -> str:
    return "job {} is {}".format(row[0], row[1])


def select_columns(wanted: list[str]) -> list[str]:
    # Column names come from the fixed allow-list, never from the request.
    return [c for c in COLUMNS if c in wanted]
