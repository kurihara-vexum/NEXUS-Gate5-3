from __future__ import annotations

import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from nexus.db import connect, initialize
from nexus.service import BusinessError, DatabaseError, MESSAGES, checkout, dashboard, return_device


NOW = datetime(2026, 9, 27, 12, 0, tzinfo=ZoneInfo("Asia/Tokyo"))


class LendingServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "test.sqlite3"
        initialize(self.database)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def checkout(self, **overrides):
        values = {
            "device_id": 1,
            "authenticated_user_id": 1,
            "due_date": "2026-09-28",
            "purpose": "社外打ち合わせ",
            "now": NOW,
        }
        values.update(overrides)
        return checkout(self.database, **values)

    def test_normal_checkout_updates_both_tables(self) -> None:
        message = self.checkout()
        self.assertIn("PC-0001", message)
        with connect(self.database) as connection:
            self.assertEqual(connection.execute("SELECT status FROM devices WHERE id = 1").fetchone()[0], "LENT")
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM lendings WHERE device_id = 1 AND returned_at IS NULL").fetchone()[0], 1)

    def test_already_lent_device_is_rejected_without_changes(self) -> None:
        with self.assertRaisesRegex(BusinessError, MESSAGES["already_lent"]):
            self.checkout(device_id=2)
        with connect(self.database) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM lendings WHERE device_id = 2 AND returned_at IS NULL").fetchone()[0], 1)

    def test_inactive_employee_is_rejected(self) -> None:
        with self.assertRaisesRegex(BusinessError, MESSAGES["inactive_employee"]):
            self.checkout(authenticated_user_id=3)
        self.assertEqual(dashboard(self.database, 3)["devices"][0]["status"], "AVAILABLE")

    def test_past_due_date_is_rejected_for_required_photo(self) -> None:
        with self.assertRaisesRegex(BusinessError, MESSAGES["past_due_date"]):
            self.checkout(due_date="2026-09-26")
        self.assertEqual(dashboard(self.database, 1)["devices"][0]["status"], "AVAILABLE")

    def test_emoji_in_purpose_is_rejected_without_saving(self) -> None:
        with self.assertRaisesRegex(BusinessError, MESSAGES["face_mark_not_allowed"]):
            self.checkout(purpose="社外打ち合わせ 😊")
        with connect(self.database) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM lendings WHERE device_id = 1").fetchone()[0], 0)

    def test_kaomoji_in_purpose_is_rejected_without_saving(self) -> None:
        for value in ("動作確認 (^_^)", "調査中 (´・ω・`)", "確認 >_<"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(BusinessError, MESSAGES["face_mark_not_allowed"]):
                    self.checkout(purpose=value)
        with connect(self.database) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM lendings WHERE device_id = 1").fetchone()[0], 0)

    def test_concurrent_checkout_creates_only_one_open_lending(self) -> None:
        def attempt(user_id: int) -> str:
            try:
                return checkout(
                    self.database,
                    device_id=1,
                    authenticated_user_id=user_id,
                    due_date="2026-09-28",
                    purpose="競合テスト",
                    now=NOW,
                )
            except BusinessError as error:
                return str(error)

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(attempt, [1, 2]))
        self.assertEqual(sum("貸し出しました" in result for result in results), 1)
        self.assertEqual(sum(MESSAGES["already_lent"] in result for result in results), 1)
        with connect(self.database) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM lendings WHERE device_id = 1 AND returned_at IS NULL").fetchone()[0], 1)

    def test_checkout_rolls_back_if_second_update_fails(self) -> None:
        with self.assertRaisesRegex(DatabaseError, MESSAGES["database_failure"]):
            self.checkout(fail_after_lending=True)
        with connect(self.database) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM lendings WHERE device_id = 1").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT status FROM devices WHERE id = 1").fetchone()[0], "AVAILABLE")

    def test_return_rolls_back_if_device_update_fails(self) -> None:
        with self.assertRaisesRegex(DatabaseError, MESSAGES["database_failure"]):
            return_device(
                self.database,
                device_id=2,
                authenticated_user_id=2,
                now=NOW,
                fail_after_lending_update=True,
            )
        with connect(self.database) as connection:
            self.assertIsNone(connection.execute("SELECT returned_at FROM lendings WHERE device_id = 2").fetchone()[0])
            self.assertEqual(connection.execute("SELECT status FROM devices WHERE id = 2").fetchone()[0], "LENT")

    def test_database_unique_constraint_is_final_double_lending_guard(self) -> None:
        with connect(self.database) as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """INSERT INTO lendings
                       (device_id, user_id, lent_at, due_date, returned_at, purpose)
                       VALUES (2, 1, ?, '2026-10-05', NULL, '重複')""",
                    (NOW.isoformat(),),
                )


if __name__ == "__main__":
    unittest.main()
