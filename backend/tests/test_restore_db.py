"""Tests for safe restoration of SQLite backups."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from restore_db import restore_backup, verify_database


def create_database(path: Path, value: str) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE records (value TEXT NOT NULL)")
        connection.execute("INSERT INTO records VALUES (?)", (value,))
        connection.commit()
    finally:
        connection.close()


class DatabaseRestoreTests(unittest.TestCase):
    def test_restore_requires_replace_and_preserves_the_previous_database(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jalsethu-restore-test-") as temporary_directory:
            root = Path(temporary_directory)
            backup = root / "backup.sqlite3"
            destination = root / "live.sqlite3"
            create_database(backup, "from-backup")
            create_database(destination, "existing-live-data")

            with self.assertRaises(FileExistsError):
                restore_backup(backup, destination)

            recovery_copy = restore_backup(backup, destination, replace=True)

            self.assertIsNotNone(recovery_copy)
            verify_database(destination)
            with closing(sqlite3.connect(destination)) as connection:
                self.assertEqual(connection.execute("SELECT value FROM records").fetchone()[0], "from-backup")
            with closing(sqlite3.connect(recovery_copy)) as connection:
                self.assertEqual(connection.execute("SELECT value FROM records").fetchone()[0], "existing-live-data")

    def test_restore_validates_source_before_touching_destination(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jalsethu-restore-test-") as temporary_directory:
            root = Path(temporary_directory)
            invalid_backup = root / "invalid.sqlite3"
            destination = root / "live.sqlite3"
            invalid_backup.write_bytes(b"not a sqlite database")
            create_database(destination, "keep-me")

            with self.assertRaises(sqlite3.DatabaseError):
                restore_backup(invalid_backup, destination, replace=True)

            with closing(sqlite3.connect(destination)) as connection:
                self.assertEqual(connection.execute("SELECT value FROM records").fetchone()[0], "keep-me")

    def test_restore_can_recover_over_a_corrupt_database_while_preserving_raw_files(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jalsethu-restore-test-") as temporary_directory:
            root = Path(temporary_directory)
            backup = root / "backup.sqlite3"
            destination = root / "live.sqlite3"
            create_database(backup, "recovered")
            destination.write_bytes(b"corrupt live database")
            Path(f"{destination}-wal").write_bytes(b"preserved wal bytes")

            recovery_copy = restore_backup(backup, destination, replace=True)

            self.assertIsNotNone(recovery_copy)
            self.assertEqual(recovery_copy.read_bytes(), b"corrupt live database")
            self.assertEqual(Path(f"{recovery_copy}-wal").read_bytes(), b"preserved wal bytes")
            verify_database(destination)
            with closing(sqlite3.connect(destination)) as connection:
                self.assertEqual(connection.execute("SELECT value FROM records").fetchone()[0], "recovered")


if __name__ == "__main__":
    unittest.main()
