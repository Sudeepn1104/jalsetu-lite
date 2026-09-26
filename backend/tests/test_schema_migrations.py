"""Legacy database migration tests for order event history."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]


class SchemaMigrationTests(unittest.TestCase):
    def test_legacy_orders_and_events_migrate_without_losing_history(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jalsethu-migration-test-") as temporary_directory:
            database = Path(temporary_directory) / "legacy.sqlite3"
            connection = sqlite3.connect(database)
            try:
                connection.executescript(
                    """
                    CREATE TABLE orders (
                        id TEXT PRIMARY KEY,
                        status TEXT NOT NULL,
                        operator_id TEXT NOT NULL,
                        capacity_l INTEGER NOT NULL,
                        water_type TEXT NOT NULL,
                        price INTEGER NOT NULL,
                        otp_hash TEXT,
                        otp_attempts INTEGER NOT NULL DEFAULT 0,
                        backup_phone TEXT,
                        meter_before INTEGER,
                        meter_after INTEGER,
                        litres_delivered INTEGER,
                        lat REAL NOT NULL,
                        lng REAL NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    CREATE TABLE order_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        order_id TEXT NOT NULL,
                        from_status TEXT,
                        to_status TEXT NOT NULL,
                        at TEXT NOT NULL
                    );
                    """
                )
                connection.execute(
                    """
                    INSERT INTO orders (
                        id, status, operator_id, capacity_l, water_type, price, lat, lng, created_at
                    ) VALUES ('JS-LEGACY', 'CONFIRMED', 'A', 4000, 'fresh', 500, 12.9, 74.8, '2026-01-01T00:00:00+00:00')
                    """
                )
                connection.executemany(
                    "INSERT INTO order_events (id, order_id, from_status, to_status, at) VALUES (?, ?, ?, ?, ?)",
                    [
                        (7, "JS-LEGACY", None, "OFFERED", "2026-01-01T00:00:00+00:00"),
                        (8, "JS-MISSING", "OFFERED", "CONFIRMED", "2026-01-01T00:01:00+00:00"),
                    ],
                )
                connection.commit()
            finally:
                connection.close()

            environment = os.environ.copy()
            environment.update(
                {
                    "JALSETHU_DB_PATH": str(database),
                    "JALSETHU_ENV": "test",
                    "JALSETHU_CORS_ORIGINS": "http://127.0.0.1",
                }
            )
            for _ in range(2):
                migrated = subprocess.run(
                    [sys.executable, "-c", "import main"],
                    cwd=BACKEND_DIR,
                    env=environment,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(migrated.returncode, 0, migrated.stderr)

            connection = sqlite3.connect(database)
            connection.row_factory = sqlite3.Row
            try:
                connection.execute("PRAGMA foreign_keys = ON")
                order = connection.execute(
                    "SELECT order_id, status FROM orders WHERE order_id = 'JS-LEGACY'"
                ).fetchone()
                self.assertEqual(tuple(order), ("JS-LEGACY", "CONFIRMED"))
                events = connection.execute(
                    "SELECT id, order_id, to_status FROM order_events ORDER BY id"
                ).fetchall()
                self.assertEqual([tuple(event) for event in events], [(7, "JS-LEGACY", "OFFERED")])
                orphan = connection.execute(
                    "SELECT original_event_id, order_id, to_status FROM order_event_orphans"
                ).fetchall()
                self.assertEqual([tuple(event) for event in orphan], [(8, "JS-MISSING", "CONFIRMED")])
                foreign_keys = connection.execute("PRAGMA foreign_key_list(order_events)").fetchall()
                self.assertTrue(
                    any(
                        row["table"] == "orders"
                        and row["from"] == "order_id"
                        and row["to"] == "order_id"
                        and row["on_delete"].upper() == "CASCADE"
                        for row in foreign_keys
                    )
                )
                self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])

                connection.execute("DELETE FROM orders WHERE order_id = 'JS-LEGACY'")
                connection.commit()
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM order_events").fetchone()[0], 0)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM order_event_orphans").fetchone()[0], 1)
            finally:
                connection.close()


if __name__ == "__main__":
    unittest.main()
