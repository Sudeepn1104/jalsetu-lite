"""Create a consistent SQLite backup while JalSetu is running.

Usage: python backup_db.py --destination /path/to/backups
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


def create_backup(source: Path, destination_dir: Path) -> Path:
    source = source.expanduser().resolve()
    destination_dir = destination_dir.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Database does not exist: {source}")
    if destination_dir.exists() and not destination_dir.is_dir():
        raise ValueError(f"Backup destination is not a directory: {destination_dir}")

    destination_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination = destination_dir / f"jalsetu-{timestamp}.sqlite3"
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=".jalsetu-backup-", suffix=".tmp", dir=destination_dir, delete=False
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)

        with closing(sqlite3.connect(source, timeout=30)) as source_connection:
            with closing(sqlite3.connect(temporary_path, timeout=30)) as backup_connection:
                source_connection.backup(backup_connection)
                check = backup_connection.execute("PRAGMA integrity_check").fetchone()
                if check is None or check[0] != "ok":
                    raise sqlite3.DatabaseError(f"Backup integrity check failed: {check[0] if check else 'no result'}")
                backup_connection.commit()

        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, destination)
        temporary_path = None
        return destination
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Create and verify a consistent backup of JalSetu's SQLite database.")
    parser.add_argument(
        "--source",
        type=Path,
        default=Path(os.environ.get("JALSETHU_DB_PATH", Path(__file__).resolve().parent / "jalsetu.db")),
        help="SQLite database file (defaults to JALSETHU_DB_PATH or backend/jalsetu.db)",
    )
    parser.add_argument("--destination", type=Path, required=True, help="Directory for the backup file")
    args = parser.parse_args()
    backup = create_backup(args.source, args.destination)
    print(f"Verified SQLite backup created: {backup}")


if __name__ == "__main__":
    main()
