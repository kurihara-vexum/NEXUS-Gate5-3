from __future__ import annotations

import sqlite3
from pathlib import Path


SCHEMA = """
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;

CREATE TABLE IF NOT EXISTS employees (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    department TEXT NOT NULL,
    employment_status TEXT NOT NULL
        CHECK (employment_status IN ('ACTIVE', 'LEAVE', 'RETIRED'))
);

CREATE TABLE IF NOT EXISTS devices (
    id INTEGER PRIMARY KEY,
    asset_no TEXT NOT NULL UNIQUE,
    model_name TEXT NOT NULL,
    device_type TEXT NOT NULL
        CHECK (device_type IN ('LAPTOP', 'TABLET', 'MONITOR')),
    status TEXT NOT NULL
        CHECK (status IN ('AVAILABLE', 'LENT', 'REPAIR', 'DISPOSED')),
    purchased_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS lendings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id INTEGER NOT NULL REFERENCES devices(id),
    user_id INTEGER NOT NULL REFERENCES employees(id),
    lent_at TEXT NOT NULL,
    due_date TEXT NOT NULL,
    returned_at TEXT,
    purpose TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS one_open_lending_per_device
ON lendings(device_id) WHERE returned_at IS NULL;
"""


def connect(database_path: str | Path) -> sqlite3.Connection:
    connection = sqlite3.connect(database_path, timeout=5, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def initialize(database_path: str | Path) -> None:
    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as connection:
        connection.executescript(SCHEMA)
        if connection.execute("SELECT COUNT(*) FROM employees").fetchone()[0] == 0:
            connection.executemany(
                "INSERT INTO employees VALUES (?, ?, ?, ?)",
                [
                    (1, "佐藤 花子", "営業部", "ACTIVE"),
                    (2, "鈴木 一郎", "開発部", "ACTIVE"),
                    (3, "休職中 利用者", "総務部", "LEAVE"),
                    (4, "退職済み 利用者", "営業部", "RETIRED"),
                ],
            )
        if connection.execute("SELECT COUNT(*) FROM devices").fetchone()[0] == 0:
            connection.executemany(
                "INSERT INTO devices VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (1, "PC-0001", "MacBook Air 13", "LAPTOP", "AVAILABLE", "2025-04-01"),
                    (2, "PC-0002", "ThinkPad X1 Carbon", "LAPTOP", "LENT", "2025-05-12"),
                    (3, "PC-0003", "Dell Latitude 7350", "LAPTOP", "AVAILABLE", "2026-01-20"),
                    (4, "PC-0004", "Surface Laptop", "LAPTOP", "REPAIR", "2024-10-05"),
                ],
            )
            connection.execute(
                """INSERT INTO lendings
                   (device_id, user_id, lent_at, due_date, returned_at, purpose)
                   VALUES (2, 2, '2026-09-25T09:00:00+09:00', '2026-10-03', NULL, '顧客訪問')"""
            )


def reset(database_path: str | Path) -> None:
    path = Path(database_path)
    if path.exists():
        path.unlink()
    initialize(path)
