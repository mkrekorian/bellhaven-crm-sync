"""Durable record of every reviewer decision.

This is what makes re-runs safe. A proposal key is a stable hash of the
change itself, so a second run recognises anything already approved or
rejected and does not put it back in the queue.
"""

import json
import os
import sqlite3
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, "data", "decisions.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    key            TEXT PRIMARY KEY,
    proposal_type  TEXT,
    account_id     TEXT,
    slug           TEXT,
    decision       TEXT,          -- approved | rejected
    decided_at     TEXT,
    payload        TEXT,          -- the proposal as shown to the reviewer
    result         TEXT           -- API responses, or the rejection reason
);
CREATE TABLE IF NOT EXISTS created_accounts (
    slug           TEXT PRIMARY KEY,
    account_id     TEXT,
    created_at     TEXT,
    reason         TEXT
);
"""


def connect():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def decided_keys(conn):
    return {row["key"] for row in conn.execute("SELECT key FROM decisions")}


def decisions(conn):
    return [dict(row) for row in conn.execute(
        "SELECT * FROM decisions ORDER BY decided_at DESC")]


def record(conn, proposal, decision, result):
    conn.execute(
        "INSERT OR REPLACE INTO decisions "
        "(key, proposal_type, account_id, slug, decision, decided_at, payload, result)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (
            proposal["key"],
            proposal["type"],
            proposal.get("account_id"),
            proposal.get("slug"),
            decision,
            time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            json.dumps(proposal),
            json.dumps(result),
        ),
    )
    conn.commit()


def remember_created(conn, slug, account_id, reason):
    conn.execute(
        "INSERT OR REPLACE INTO created_accounts (slug, account_id, created_at, reason)"
        " VALUES (?,?,?,?)",
        (slug, account_id, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), reason),
    )
    conn.commit()


def created_accounts(conn):
    return {row["slug"]: row["account_id"]
            for row in conn.execute("SELECT slug, account_id FROM created_accounts")}
