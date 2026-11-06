"""Generate a reviewable, add-only PostgreSQL migration for the operational portal.

Run:
  python scripts/generate_platform_schema.py > migration_platform_v2.sql

The generated SQL must be reviewed before applying it through the Supabase
migration interface. Never run generated schema changes from a web request.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Select PG schema without making any network connection or fetching credentials.
os.environ["DATABASE_URL"] = "postgresql://offline_generator@localhost/noor"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable
from app import Base

dialect = postgresql.dialect()
v2_tables = [t for t in Base.metadata.sorted_tables if t.name.startswith("v2_")]
assert v2_tables, "No version 2 tables discovered"
print("BEGIN;")
for table in v2_tables:
    print(str(CreateTable(table, if_not_exists=True).compile(dialect=dialect)).rstrip() + ";")
    for index in table.indexes:
        print(str(CreateIndex(index, if_not_exists=True).compile(dialect=dialect)).rstrip() + ";")
    identifier = f'"{table.schema}"."{table.name}"'
    print(f"ALTER TABLE {identifier} ENABLE ROW LEVEL SECURITY;")
    print(f"REVOKE ALL ON TABLE {identifier} FROM anon, authenticated;")
print("COMMIT;")
