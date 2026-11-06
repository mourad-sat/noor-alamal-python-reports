"""Encrypted backups and offline integrity checks for the Noor Al Amal database.

No database credentials are stored here. Place the Fernet key in a protected
GitHub Actions secret. Keep an offline copy of the key, because restoration is
impossible without it.

Usage:
    python scripts/backup_crypto.py encrypt backup.dump backup.dump.enc
    python scripts/backup_crypto.py verify backup.dump.enc
    python scripts/backup_crypto.py decrypt backup.dump.enc backup.dump
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

MAX_INPUT_BYTES = 512 * 1024 * 1024


def cipher():
    value = os.environ.get("BACKUP_FERNET_KEY", "")
    if not value:
        raise SystemExit("BACKUP_FERNET_KEY secret is missing")
    try:
        return Fernet(value.encode("ascii"))
    except (ValueError, TypeError) as exc:
        raise SystemExit("BACKUP_FERNET_KEY must be a valid 32-byte Fernet key") from exc


def read_limited(path: Path) -> bytes:
    with path.open("rb") as handle:
        value = handle.read(MAX_INPUT_BYTES + 1)
    if not value:
        raise SystemExit("Backup file is empty")
    if len(value) > MAX_INPUT_BYTES:
        raise SystemExit("Backup exceeds the approved 512 MiB encryption limit")
    return value


def main():
    if len(sys.argv) not in (3, 4):
        raise SystemExit("Usage: backup_crypto.py encrypt|decrypt|verify input [output]")
    op = sys.argv[1]
    source = Path(sys.argv[2])
    crypt = cipher()
    if op == "encrypt" and len(sys.argv) == 4:
        plaintext = read_limited(source)
        Path(sys.argv[3]).write_bytes(crypt.encrypt(plaintext))
        print("Encrypted backup created")
    elif op in ("decrypt", "verify"):
        encrypted = read_limited(source)
        try:
            plaintext = crypt.decrypt(encrypted)
        except InvalidToken as exc:
            raise SystemExit("Encrypted backup integrity verification failed") from exc
        if not plaintext.startswith(b"PGDMP"):
            raise SystemExit("Backup is not a PostgreSQL custom-format dump")
        if op == "decrypt" and len(sys.argv) == 4:
            Path(sys.argv[3]).write_bytes(plaintext)
            print("Backup decrypted and header verified")
        elif op == "verify" and len(sys.argv) == 3:
            print("Backup integrity and PostgreSQL header verified")
        else:
            raise SystemExit("Invalid number of arguments")
    else:
        raise SystemExit("Unknown operation")


if __name__ == "__main__":
    main()
