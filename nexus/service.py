from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from .db import connect


JST = ZoneInfo("Asia/Tokyo")


class BusinessError(Exception):
    pass


class DatabaseError(Exception):
    pass


MESSAGES = {
    "already_lent": "この端末は現在貸出中です。別の端末を選択してください。",
    "inactive_employee": "貸出申請の権限がありません。",
    "past_due_date": "明日以降の日付を入力してください。",
    "unavailable": "この端末は現在利用できません。別の端末を選択してください。",
    "not_found": "指定された端末または社員が見つかりません。",
    "loan_success": "{asset_no} を貸し出しました。返却予定日は {due_date} です。",
    "return_success": "{asset_no} を返却しました。",
    "return_denied": "この端末を返却する権限がありません。",
    "database_failure": "データの更新に失敗しました。入力内容は保存されていません。もう一度お試しください。",
}


def _today(now: datetime | None = None) -> date:
    return (now or datetime.now(JST)).astimezone(JST).date()


def _transaction(connection: sqlite3.Connection, action: Callable[[], str]) -> str:
    connection.execute("BEGIN IMMEDIATE")
    try:
        result = action()
        connection.execute("COMMIT")
        return result
    except Exception:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise


def dashboard(database_path: str | Path, authenticated_user_id: int) -> dict:
    with connect(database_path) as connection:
        employee = connection.execute(
            "SELECT id, name, department, employment_status FROM employees WHERE id = ?",
            (authenticated_user_id,),
        ).fetchone()
        devices = connection.execute(
            """SELECT d.*, l.user_id AS borrower_id, l.due_date, e.name AS borrower_name
               FROM devices d
               LEFT JOIN lendings l ON l.device_id = d.id AND l.returned_at IS NULL
               LEFT JOIN employees e ON e.id = l.user_id
               ORDER BY d.asset_no"""
        ).fetchall()
        return {
            "employee": dict(employee) if employee else None,
            "devices": [dict(row) for row in devices],
        }


def checkout(
    database_path: str | Path,
    *,
    device_id: int,
    authenticated_user_id: int,
    due_date: str,
    purpose: str,
    now: datetime | None = None,
    fail_after_lending: bool = False,
) -> str:
    try:
        requested_due_date = date.fromisoformat(due_date)
    except ValueError as error:
        raise BusinessError(MESSAGES["past_due_date"]) from error
    if requested_due_date <= _today(now):
        raise BusinessError(MESSAGES["past_due_date"])

    try:
        with connect(database_path) as connection:
            def action() -> str:
                employee = connection.execute(
                    "SELECT * FROM employees WHERE id = ?", (authenticated_user_id,)
                ).fetchone()
                device = connection.execute(
                    "SELECT * FROM devices WHERE id = ?", (device_id,)
                ).fetchone()
                if employee is None or device is None:
                    raise BusinessError(MESSAGES["not_found"])
                if employee["employment_status"] != "ACTIVE":
                    raise BusinessError(MESSAGES["inactive_employee"])

                open_lending = connection.execute(
                    "SELECT id FROM lendings WHERE device_id = ? AND returned_at IS NULL",
                    (device_id,),
                ).fetchone()
                if open_lending is not None or device["status"] == "LENT":
                    raise BusinessError(MESSAGES["already_lent"])
                if device["status"] != "AVAILABLE":
                    raise BusinessError(MESSAGES["unavailable"])

                timestamp = (now or datetime.now(JST)).astimezone(JST).isoformat(timespec="seconds")
                connection.execute(
                    """INSERT INTO lendings
                       (device_id, user_id, lent_at, due_date, returned_at, purpose)
                       VALUES (?, ?, ?, ?, NULL, ?)""",
                    (device_id, authenticated_user_id, timestamp, due_date, purpose.strip()),
                )
                if fail_after_lending:
                    raise sqlite3.OperationalError("injected failure")
                updated = connection.execute(
                    "UPDATE devices SET status = 'LENT' WHERE id = ? AND status = 'AVAILABLE'",
                    (device_id,),
                )
                if updated.rowcount != 1:
                    raise BusinessError(MESSAGES["already_lent"])
                return MESSAGES["loan_success"].format(
                    asset_no=device["asset_no"], due_date=due_date.replace("-", "/")
                )

            return _transaction(connection, action)
    except BusinessError:
        raise
    except (sqlite3.Error, OSError) as error:
        raise DatabaseError(MESSAGES["database_failure"]) from error


def return_device(
    database_path: str | Path,
    *,
    device_id: int,
    authenticated_user_id: int,
    now: datetime | None = None,
    fail_after_lending_update: bool = False,
) -> str:
    try:
        with connect(database_path) as connection:
            def action() -> str:
                device = connection.execute(
                    "SELECT * FROM devices WHERE id = ?", (device_id,)
                ).fetchone()
                lending = connection.execute(
                    """SELECT * FROM lendings
                       WHERE device_id = ? AND returned_at IS NULL""",
                    (device_id,),
                ).fetchone()
                if device is None or lending is None or lending["user_id"] != authenticated_user_id:
                    raise BusinessError(MESSAGES["return_denied"])
                timestamp = (now or datetime.now(JST)).astimezone(JST).isoformat(timespec="seconds")
                connection.execute(
                    "UPDATE lendings SET returned_at = ? WHERE id = ? AND returned_at IS NULL",
                    (timestamp, lending["id"]),
                )
                if fail_after_lending_update:
                    raise sqlite3.OperationalError("injected failure")
                connection.execute(
                    "UPDATE devices SET status = 'AVAILABLE' WHERE id = ?", (device_id,)
                )
                return MESSAGES["return_success"].format(asset_no=device["asset_no"])

            return _transaction(connection, action)
    except BusinessError:
        raise
    except (sqlite3.Error, OSError) as error:
        raise DatabaseError(MESSAGES["database_failure"]) from error
