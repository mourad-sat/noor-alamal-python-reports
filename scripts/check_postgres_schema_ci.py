"""Exercise the generated schema against a real PostgreSQL service.

This script runs exclusively on the isolated disposable GitHub Actions
PostgreSQL service, never against a production database.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import psycopg2

dsn = os.environ.get("POSTGRES_TEST_URL")
if not dsn or "localhost" not in dsn:
    raise SystemExit("POSTGRES_TEST_URL must target the disposable localhost CI service")

schema_sql = subprocess.check_output(
    [sys.executable, "scripts/generate_platform_schema.py"], text=True
)
if "ENABLE ROW LEVEL SECURITY" not in schema_sql:
    raise SystemExit("Required RLS statements are missing from generated schema")

with psycopg2.connect(dsn) as connection:
    with connection.cursor() as cursor:
        cursor.execute("CREATE SCHEMA IF NOT EXISTS noor_app")
        cursor.execute("CREATE ROLE anon NOLOGIN")
        cursor.execute("CREATE ROLE authenticated NOLOGIN")
        cursor.execute("CREATE ROLE noor_reports_web NOLOGIN")
        cursor.execute(schema_sql)
        cursor.execute(
            """
            SELECT c.relname, c.relrowsecurity
            FROM pg_class AS c
            JOIN pg_namespace AS n ON n.oid = c.relnamespace
            WHERE n.nspname = 'noor_app' AND c.relkind = 'r'
              AND c.relname LIKE 'v2_%'
            ORDER BY c.relname
            """
        )
        tables = cursor.fetchall()
        assert len(tables) >= 20, f"Expected >=20 normalized tables, got {len(tables)}"
        assert all(rls for _, rls in tables), "One or more private tables have RLS disabled"
        for name, _ in tables:
            cursor.execute(
                "SELECT has_table_privilege('anon', %s, 'SELECT'), "
                "has_table_privilege('authenticated', %s, 'SELECT')",
                (f"noor_app.{name}", f"noor_app.{name}"),
            )
            public_allowed, signed_in_allowed = cursor.fetchone()
            assert not public_allowed and not signed_in_allowed, (
                f"Unsafe direct client read on {name}"
            )
        cursor.execute(
            """
            SELECT COUNT(*)
            FROM pg_constraint AS con
            JOIN pg_namespace AS ns ON ns.oid = con.connamespace
            WHERE ns.nspname = 'noor_app'
              AND con.contype = 'f' AND con.conname LIKE '%v2%'
            """
        )
        fk_count = cursor.fetchone()[0]
        assert fk_count >= 15, f"Missing foreign-key constraints: {fk_count}"

        # Exercise access from the actual restricted role, not only owner postgres.
        cursor.execute("GRANT USAGE ON SCHEMA noor_app TO noor_reports_web")
        cursor.execute("GRANT SELECT ON noor_app.v2_centers TO noor_reports_web")
        cursor.execute(
            "CREATE POLICY ci_read_centers ON noor_app.v2_centers "
            "FOR SELECT TO noor_reports_web USING (true)"
        )
        cursor.execute("SET ROLE noor_reports_web")
        cursor.execute("SELECT count(*) FROM noor_app.v2_centers")
        assert cursor.fetchone()[0] == 0
        cursor.execute("RESET ROLE")
print(f"PostgreSQL: {len(tables)} private RLS-enabled v2 tables; {fk_count} foreign keys verified")


# Run the FastAPI application on the actual disposable PostgreSQL schema.
# The RLS client role is assumed for every new SQLAlchemy connection, so this
# also checks server-side access under a NOLOGIN, non-bypassrls database role.
import hashlib
from sqlalchemy import event
from fastapi.testclient import TestClient

os.environ["DATABASE_URL"] = dsn
os.environ["APP_SECRET"] = "ephemeral-github-ci-only-secret-no-production-use"
salt = b"local-test-salt"
digest = hashlib.pbkdf2_hmac("sha256", b"ci-password-strong", salt, 390000)
os.environ["ADMIN_PASSWORD_HASH"] = (
    "pbkdf2_sha256$390000$" + salt.hex() + "$" + digest.hex()
)
os.environ["ADMIN_USERNAME"] = "ci-admin"
os.environ.pop("VERCEL", None)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as site

site.Report.__table__.create(bind=site.engine, checkfirst=True)
site.Absence.__table__.create(bind=site.engine, checkfirst=True)

@event.listens_for(site.engine, "connect")
def set_test_role(connection, connection_record):
    cursor=connection.cursor()
    try:
        cursor.execute("SET ROLE noor_reports_web")
        cursor.execute("SELECT current_user")
        assert cursor.fetchone()[0] == "noor_reports_web"
    finally:
        cursor.close()

with TestClient(site.app, follow_redirects=False) as client:
    health=client.get("/health")
    assert health.status_code==200,health.text
    assert health.json()["status"]=="ready"
    login=client.post("/login",data={"username":"ci-admin","password":"ci-password-strong"})
    assert login.status_code==303,login.text
    dashboard=client.get("/platform")
    assert dashboard.status_code==200,dashboard.text[:300]
    assert "دار الشباب لعيايدة 2" in dashboard.text
    assert "مركز حمزة عبد المطلب" in dashboard.text
print("PostgreSQL FastAPI smoke test: ready, authenticated, restricted-role access")
