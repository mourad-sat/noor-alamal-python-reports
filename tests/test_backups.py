"""Verify encrypted backup payloads cannot be read or modified without a key."""
import base64
import os
import subprocess
import sys
from pathlib import Path


def run_crypto(*args, env):
    return subprocess.run(
        [sys.executable, "scripts/backup_crypto.py", *map(str, args)],
        text=True, capture_output=True, env=env, timeout=15,
    )


def test_encrypted_backup_roundtrip_and_tamper_rejection(tmp_path):
    from cryptography.fernet import Fernet
    key=Fernet.generate_key().decode("ascii")
    env={**os.environ,"BACKUP_FERNET_KEY":key}
    source=tmp_path/"archive.dump"
    encrypted=tmp_path/"archive.dump.enc"
    restored=tmp_path/"restored.dump"
    source.write_bytes(b"PGDMP" + bytes(range(128)))
    result=run_crypto("encrypt",source,encrypted,env=env)
    assert result.returncode==0,result.stderr
    assert source.read_bytes() not in encrypted.read_bytes()
    assert run_crypto("verify",encrypted,env=env).returncode==0
    assert run_crypto("decrypt",encrypted,restored,env=env).returncode==0
    assert restored.read_bytes()==source.read_bytes()
    tampered=bytearray(encrypted.read_bytes())
    tampered[-10]^=1
    encrypted.write_bytes(tampered)
    bad=run_crypto("verify",encrypted,env=env)
    assert bad.returncode!=0
    assert "integrity" in bad.stderr.lower() or "integrity" in bad.stdout.lower()


def test_invalid_or_missing_backup_key_fails_closed(tmp_path):
    source=tmp_path/"archive.dump"
    source.write_bytes(b"PGDMP"+b"x"*300)
    env={**os.environ}
    env.pop("BACKUP_FERNET_KEY",None)
    assert run_crypto("encrypt",source,tmp_path/"enc",env=env).returncode!=0
    env["BACKUP_FERNET_KEY"]="invalid"
    assert run_crypto("encrypt",source,tmp_path/"enc",env=env).returncode!=0
