"""Tests for SQLite online backup and integrity verification."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from backup_db import create_backup


class DatabaseBackupTests(unittest.TestCase):
    def test_backup_contains_consistent_rows_and_passes_integrity_check(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jalsethu-backup-test-") as temporary_directory:
            root = Path(temporary_directory)
            source = root / "live.sqlite3"
            destination_dir = root / "backups"
            connection = sqlite3.connect(source)
            try:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("CREATE TABLE orders (order_id TEXT PRIMARY KEY, status TEXT NOT NULL)")
                connection.executemany(
                    "INSERT INTO orders VALUES (?, ?)",
                    [("JS-1", "CONFIRMED"), ("JS-2", "DELIVERED")],
                )
                connection.commit()
                backup = create_backup(source, destination_dir)
                self.assertTrue(backup.is_file())
                self.assertEqual(backup.parent, destination_dir.resolve())
                backup_connection = sqlite3.connect(backup)
                try:
                    self.assertEqual(backup_connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                    self.assertEqual(
                        backup_connection.execute("SELECT order_id, status FROM orders ORDER BY order_id").fetchall(),
                        [("JS-1", "CONFIRMED"), ("JS-2", "DELIVERED")],
                    )
                finally:
                    backup_connection.close()
            finally:
                connection.close()

    def test_backup_rejects_missing_sources_and_non_directory_destinations(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jalsethu-backup-test-") as temporary_directory:
            root = Path(temporary_directory)
            source = root / "live.sqlite3"
            connection = sqlite3.connect(source)
            connection.close()
            with self.assertRaises(FileNotFoundError):
                create_backup(root / "missing.sqlite3", root / "backups")
            with self.assertRaises(ValueError):
                create_backup(source, source)


if __name__ == "__main__":
    unittest.main()
