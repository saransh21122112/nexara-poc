"""Read-only database access (vetted block). SQLite is built in; Postgres and MySQL need their driver (psycopg / pymysql) and always
connect read-only, with a statement timeout, and with TLS for anything that is not localhost."""
import sqlite3
import time
from typing import NamedTuple
from urllib.parse import urlparse


class DbError(Exception):
    """Message is safe to show to the model."""


class Result(NamedTuple):
    columns: list
    rows: list
    truncated: bool


import re as _re

_IDENT = _re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")


def _ident(name):
    if not isinstance(name, str) or not _IDENT.fullmatch(name):
        raise DbError("Invalid table name.")
    return name


class _SqliteDb:
    def __init__(self, path, timeout_s, max_rows):
        import os
        if not os.path.isfile(path):
            raise DbError("The SQLite database file was not found.")
        self.conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
        self.conn.execute("PRAGMA query_only=ON")
        self.timeout_s, self.max_rows = timeout_s, max_rows

    def columns(self, table):
        """Every real column name of `table` (readable or not): the SQL guard uses it so an alias can never shadow one."""
        rows = self.conn.execute(f"PRAGMA table_info({_ident(table)})").fetchall()
        return [r[1] for r in rows]

    def query(self, sql, params=()):
        deadline = time.monotonic() + self.timeout_s
        self.conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)
        try:
            cur = self.conn.execute(sql, params)
            cols = [d[0] for d in cur.description or []]
            rows = cur.fetchmany(self.max_rows + 1)
        except sqlite3.OperationalError as e:
            raise DbError("The query took too long and was stopped." if "interrupt" in str(e).lower() else "The database rejected the query.") from None
        except sqlite3.Error:
            raise DbError("The database rejected the query.") from None
        finally:
            self.conn.set_progress_handler(None, 0)
        return Result(cols, [dict(zip(cols, r)) for r in rows[:self.max_rows]], len(rows) > self.max_rows)


class _PgDb:
    def __init__(self, dsn, timeout_s, max_rows):
        try:
            import psycopg
        except ImportError:
            raise DbError("The 'psycopg' package is required for PostgreSQL (see requirements.txt).") from None
        if "sslmode=disable" in dsn or "sslmode=allow" in dsn or "sslmode=prefer" in dsn:
            raise DbError("TLS is required: remove the weak sslmode from the connection string.")
        host = urlparse(dsn).hostname or ""
        kw = {"options": f"-c default_transaction_read_only=on -c statement_timeout={int(timeout_s * 1000)}", "connect_timeout": int(timeout_s)}
        if host not in ("localhost", "127.0.0.1", "::1"):
            kw["sslmode"] = "require"
        try:
            self.conn = psycopg.connect(dsn, **kw)
            self.conn.set_read_only(True)
        except Exception:                            # noqa: BLE001
            raise DbError("Could not connect to the database.") from None
        self.max_rows = max_rows

    def columns(self, table):
        r = self.query("SELECT column_name FROM information_schema.columns WHERE table_name = %s ORDER BY ordinal_position", (_ident(table),))
        return [row["column_name"] for row in r.rows]

    def query(self, sql, params=()):
        try:
            cur = self.conn.cursor()
            cur.execute(sql, params or None)
            cols = [d.name for d in cur.description or []]
            rows = cur.fetchmany(self.max_rows + 1)
            cur.close()
            self.conn.rollback() if hasattr(self.conn, "rollback") else None
        except Exception:                            # noqa: BLE001
            raise DbError("The database rejected the query or it took too long.") from None
        return Result(cols, [dict(zip(cols, r)) for r in rows[:self.max_rows]], len(rows) > self.max_rows)


class _MyDb:
    def __init__(self, dsn, timeout_s, max_rows):
        try:
            import pymysql
        except ImportError:
            raise DbError("The 'pymysql' package is required for MySQL (see requirements.txt).") from None
        u = urlparse(dsn)
        host = u.hostname or ""
        kw = {"host": host, "port": u.port or 3306, "user": u.username, "password": u.password, "database": (u.path or "/").lstrip("/") or None,
              "connect_timeout": int(timeout_s), "read_timeout": int(timeout_s), "autocommit": False, "charset": "utf8mb4"}
        if host not in ("localhost", "127.0.0.1", "::1"):
            kw["ssl"] = {"ssl": {}}
        try:
            self.conn = pymysql.connect(**kw)
            c = self.conn.cursor(); c.execute("SET SESSION TRANSACTION READ ONLY"); c.close()
        except Exception:                            # noqa: BLE001
            raise DbError("Could not connect to the database.") from None
        self.max_rows = max_rows

    def columns(self, table):
        r = self.query("SELECT column_name FROM information_schema.columns WHERE table_name = %s ORDER BY ordinal_position", (_ident(table),))
        return [row.get("column_name") or row.get("COLUMN_NAME") for row in r.rows]

    def query(self, sql, params=()):
        try:
            cur = self.conn.cursor()
            cur.execute(sql, params or None)
            cols = [d[0] for d in cur.description or []]
            rows = cur.fetchmany(self.max_rows + 1)
            cur.close(); self.conn.rollback()
        except Exception:                            # noqa: BLE001
            raise DbError("The database rejected the query or it took too long.") from None
        return Result(cols, [dict(zip(cols, r)) for r in rows[:self.max_rows]], len(rows) > self.max_rows)


def connect(kind, *, path=None, dsn=None, timeout_s=10, max_rows=1000):
    if kind == "sqlite":
        return _SqliteDb(path or "", timeout_s, max_rows)
    if kind == "postgres":
        return _PgDb(dsn or "", timeout_s, max_rows)
    if kind == "mysql":
        return _MyDb(dsn or "", timeout_s, max_rows)
    raise DbError(f"Unsupported database kind: {kind}")
