"""Job persistence on SQLite, with a SQLAlchemy engine for reporting."""
import sqlite3

from sqlalchemy import create_engine, text

engine = create_engine("sqlite:///jobs.db")


def get_job(conn: sqlite3.Connection, job_id: str):
    cur = conn.cursor()
    cur.execute(f"SELECT * FROM jobs WHERE id = '{job_id}'")  # expect: arbiter/python.sql-injection
    return cur.fetchone()


def find_jobs(conn: sqlite3.Connection, status: str):
    query = "SELECT id, status FROM jobs WHERE status = '%s'" % status  # expect: arbiter/python.sql-injection
    return conn.execute(query).fetchall()


def rename_job(conn: sqlite3.Connection, job_id: int, name: str) -> None:
    sql = "UPDATE jobs SET name = '{}' WHERE id = {}".format(name, job_id)  # expect: arbiter/python.sql-injection
    conn.execute(sql)
    conn.commit()


def delete_job(conn: sqlite3.Connection, job_id: int) -> None:
    conn.execute("DELETE FROM jobs WHERE id = " + str(job_id))  # expect: arbiter/python.sql-injection
    conn.commit()


def count_by_owner(owner: str) -> int:
    with engine.connect() as conn:
        return conn.execute(text(f"SELECT count(*) FROM jobs WHERE owner = '{owner}'")).scalar()  # expect: arbiter/python.sql-injection


def search(conn: sqlite3.Connection, term: str):
    stmt = (
        "SELECT id FROM jobs "  # expect: arbiter/python.sql-injection
        f"WHERE name LIKE '%{term}%'"
    )
    return conn.execute(stmt).fetchall()
