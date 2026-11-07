# Noor Al Amal — production release gate (2026-11-06)

**Status: NOT READY FOR PRODUCTION. Keep PR #1 as draft. Do not merge main.**

Project: Vercel `noor-alamal-python-reports`, database `mjpeudultagipkiqgyql`,
development branch `feature/platform-v2-20261106`.

## Verified so far

- Automated Python application tests pass, including multi-center access, attendance and approvals,
  CSV import, private attachment access, QR result verification, backup cryptography.
- Vercel automatically builds the development branch, reported `READY`.
- Exact preview health response on November 6: `{"service":"noor-alamal","status":"setup-required"}`.
  Therefore the preview is **not** ready for operational testing.
- The two pre-existing production tables `noor_app.reports` and `noor_app.absences` were
  empty when queried (November 6). This does **not** prove newer tables are present.
- Supabase security and health advisors had no findings for the **current** database.
  They do not validate the unreleased v2 schema.

## Progress verified on 2026-11-07

- [x] All application unit and integration tests pass after implementing
  stronger login protections, track-level schedule collision checks,
  signed-off attendance corrections and manually selected evidence.
- [x] Disposable PostgreSQL 17 integration: **28 private RLS-enabled v2 tables**,
  **40 foreign-key constraints**, restricted `noor_reports_web` server role,
  valid admin authentication and FastAPI `/health=ready`.
- [x] Encrypted PostgreSQL custom-format backup successfully decrypted and
  **restored into a separate disposable CI database**, including two center records
  and the private-table RLS flags.
- [ ] **Production backup not yet verified.** No GitHub Actions production secrets
  have been configured or exercised by this process, and no retained weekly
  archive/production restore has been demonstrated.
- [ ] **Production migration not applied.** The association database still needs
  a reviewed add-only migration, real DB-role tests and protected release gate.
- [ ] **Preview still lacks a dedicated live staging database.** CI Postgres is
  disposable; Vercel Preview must not be connected directly to production data.

## Blocking release requirements

- [ ] Provision an **isolated testing database and restricted role**. Do not point an
  unreviewed preview deployment at production data.
- [ ] Configure Preview environment variables with test-only credentials. Verify
  `/health` returns `ready`, not `setup-required`.
- [ ] Review and apply the add-only v2 SQL migration to staging first.
  Confirm RLS on all tables, application role grants and cross-center authorization tests.
  **Do not run `create_all` in production requests**.
- [ ] Test real end-to-end operations in the Vercel preview (both centers,
  coordinator/instructor/educator accounts, individual learner records, approvals).
- [ ] Configure repository Actions secrets `NOOR_BACKUP_DATABASE_URL` and
  `NOOR_BACKUP_FERNET_KEY` in GitHub (never disclose secret values in chat).
  Run the workflow on the approved production branch and verify an encrypted
  backup artifact was created. Keep the encryption key offline.
- [ ] Test real backup **restoration** to an isolated database, including attachments.
  Confirm retention of **exactly four** latest successful weekly archives.
- [ ] Implement and validate legally safe permanent learner erasure across all personal
  records, attachments, legacy data and audit outputs. Plan the handling of previously
  encrypted backups to prevent restoring erased records. Require administrator rationale.
- [ ] Confirm final official logo appears on login, dashboards, paper notices and
  approved documents.
- [ ] Confirm the bulk grade export creates an acceptable **PDF** document, and review
  whether a downloadable server-generated PDF/ZIP is required beyond browser Print → PDF.
- [ ] Verify coordinator-only attachment selection is reflected in aggregate report output.
- [ ] Test production migration additively **after** obtaining a recovery point.
- [ ] Obtain final release sign-off against the reviewed commit, verify deployment target
  production/main and retain a rollback procedure.

## Encrypted weekly backup configuration

The workflow `.github/workflows/weekly-backup.yml` runs Sunday 03:17 UTC
from the default branch and uploads only an encrypted PostgreSQL custom dump.
The last four matching GitHub Actions artifacts are retained; superseded
weekly archives are deleted. The workflow cannot succeed before its secret
configuration.

The backup includes database-backed `v2_attachment_blobs` only when those tables
have been migrated and populated. If attachments migrate to object storage,
the backup scope **must** expand to include those objects.

Create a random Fernet key locally, save it securely **outside GitHub**, and set
`NOOR_BACKUP_FERNET_KEY` to the same value as a GitHub Actions repository secret.
Set `NOOR_BACKUP_DATABASE_URL` as another repository secret; for externally hosted
GitHub runners, confirm the Postgres connection uses a reachable and secure
endpoint (Supabase direct connections may require IPv6 or an appropriate pooler).
Do not paste these values into conversations or commit them.

## Release policy

A passing CI build or a Vercel `READY` build **does not equal** production readiness.
Do not promote or merge the draft PR while any blocking item remains unchecked.
