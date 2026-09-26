"""Restore a verified SQLite backup. Stop the JalSetu service first."""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


def verify_database(database: Path) -> None:
    try:
        with closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True, timeout=30)) as connection:
            result = connection.execute("PRAGMA integrity_check").fetchone()
    except sqlite3.DatabaseError as exc:
        raise sqlite3.DatabaseError(f"Could not read SQLite database {database}: {exc}") from exc
    if result is None or result[0] != "ok":
        raise sqlite3.DatabaseError(f"Database integrity check failed: {result[0] if result else 'no result'}")


def restore_backup(source: Path, destination: Path, *, replace: bool = False) -> Path | None:
    source = source.expanduser().resolve()
    destination = destination.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Backup does not exist: {source}")
    if source == destination:
        raise ValueError("Backup and destination must be different files")
    if destination.exists() and not replace:
        raise FileExistsError(f"Destination already exists; pass --replace to preserve and replace it: {destination}")
    if destination.exists() and not destination.is_file():
        raise ValueError(f"Restore destination is not a file: {destination}")

    verify_database(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    temporary_path: Path | None = None
    recovery_copy: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=".jalsetu-restore-", suffix=".tmp", dir=destination.parent, delete=False
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)

        with closing(sqlite3.connect(source, timeout=30)) as source_connection:
            with closing(sqlite3.connect(temporary_path, timeout=30)) as staging_connection:
                source_connection.backup(staging_connection)
                staging_connection.commit()
        verify_database(temporary_path)

        if destination.exists():
            recovery_copy = destination.with_name(f"{destination.name}.pre-restore-{timestamp}")
            shutil.copy2(destination, recovery_copy)
            for suffix in ("-wal", "-shm"):
                sidecar = Path(f"{destination}{suffix}")
                if sidecar.exists():
                    shutil.copy2(sidecar, Path(f"{recovery_copy}{suffix}"))

            verified_recovery: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    prefix=".jalsetu-recovery-", suffix=".tmp", dir=destination.parent, delete=False
                ) as temporary_file:
                    verified_recovery = Path(temporary_file.name)
                with closing(sqlite3.connect(destination, timeout=30)) as current_connection:
                    with closing(sqlite3.connect(verified_recovery, timeout=30)) as recovery_connection:
                        current_connection.backup(recovery_connection)
                        recovery_connection.commit()
                verify_database(verified_recovery)
                os.replace(verified_recovery, recovery_copy)
                verified_recovery = None
                for suffix in ("-wal", "-shm"):
                    Path(f"{recovery_copy}{suffix}").unlink(missing_ok=True)
            except sqlite3.DatabaseError:
                pass
            finally:
                if verified_recovery is not None:
                    verified_recovery.unlink(missing_ok=True)

        # Restoring while the service is running is unsupported. Remove stale
        # WAL state only after the current database has a verified recovery copy.
        Path(f"{destination}-wal").unlink(missing_ok=True)
        Path(f"{destination}-shm").unlink(missing_ok=True)
        os.replace(temporary_path, destination)
        temporary_path = None
        return recovery_copy
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Restore a verified JalSetu SQLite backup.",
        epilog="Stop the JalSetu service before restoring; leaving it running may overwrite transactions.",
    )
    parser.add_argument("--source", type=Path, required=True, help="Verified SQLite backup file")
    parser.add_argument(
        "--destination",
        type=Path,
        default=Path(os.environ.get("JALSETHU_DB_PATH", Path(__file__).resolve().parent / "jalsetu.db")),
        help="Database file to restore (defaults to JALSETHU_DB_PATH or backend/jalsetu.db)",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Replace an existing database after creating a verified pre-restore copy",
    )
    args = parser.parse_args()
    recovery_copy = restore_backup(args.source, args.destination, replace=args.replace)
    if recovery_copy:
        try:
            verify_database(recovery_copy)
        except sqlite3.DatabaseError:
            print(f"Existing database was corrupt; its raw database/WAL files were preserved at: {recovery_copy}")
        else:
            print(f"Verified pre-restore database copy preserved at: {recovery_copy}")
    print(f"Verified backup restored to: {args.destination.expanduser().resolve()}")


if __name__ == "__main__":
    main()
