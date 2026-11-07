"""Exercise the generated schema against a real PostgreSQL service.

This script runs exclusively on the isolated disposable GitHub Actions
PostgreSQL service, never against a production database.
"""
from __future__ import annotations

import os
import subprocess
import sys

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
