# Noor Al Amal — production release gate (2026-11-06)

**Status: DB schema installed and verified. Public release still blocked until backups and deployment smoke checks pass. Keep PR #1 as draft.**

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

## Additional development verified on 2026-11-07

- [x] Official **user-provided association logo** installed as a real
  repository asset, included in login/navigation, guardian notices,
  grade transcripts and approved session reports.
- [x] Admin-only permanent learner deletion: delete individually linked
  records and electronic grade proofs, preserve only coarse per-year counts,
  and refuse automatic deletion when shared images/documents need manual review.
- [x] Admin-only deletion of private report attachments and entire session
  reports, with mandatory reason, explicit confirmation and non-identifying audit.
- [x] Extraordinary single-session cancellation and makeup workflow for
  center coordinators, with mandatory reasons and staff notification.
- [x] Printable approved session report with electronic approver/time,
  manual signature/stamp fields, and independent QR proof registry.
- [x] Public QR verification explicitly states that electronic-record
  integrity is verified; **a copied QR cannot establish physical paper
  authenticity without comparison against the approved original**.
- [x] **20 application tests** passed, including privacy, print proofs,
  role separation and changes to recurring schedules. PostgreSQL
  migration/smoke and encrypted backup restore also passed on disposable CI.
- [ ] **Compliance/legal release gate remains open:** encrypted historical
  backups and previously printed/downloaded copies are not physically
  erased by the active-database deletion route. Agree a retention,
  restoration-scrubbing and verified destruction process before real data.
- [ ] **Production backup remains inactive/unverified** without protected
  GitHub Actions secrets, an actual scheduled run and real isolated recovery.
- [ ] **Vercel Preview remains setup-required** until an isolated,
  functioning non-production database and credentials are configured.
- [ ] No live production schema migration or `main` merge has occurred.

## Deployment preparation — 2026-11-07 18:42 UTC

- [x] User explicitly requested the final release to start.
- [x] Exact tested migration generated in GitHub Actions run
  [42402035527](https://github.com/mourad-sat/noor-alamal-python-reports/actions/runs/42402035527)
  and checked to contain **30 additive tables, 30 RLS policies, and no destructive DDL**.
- [x] Additive migration `add_noor_v2_operational_schema_20261107`
  successfully applied to the dedicated production Supabase project
  `mjpeudultagipkiqgyql`; it did not delete or rewrite the existing two tables.
- [x] Verified all **30 new tables have RLS** and the server-role policy,
  `anon`/`authenticated` cannot directly read `v2_learners`,
  and `noor_reports_web` can read it.
- [x] Supabase security advisors returned no findings after migration.
- [x] Production deployment still at known-good commit
  `94e99e388b6bcd371c7483bb7f5d03326b87bd4b`
  and `/health` returned `ready`.
- [x] Rollback branch `rollback/pre-v2-20261107` created at the
  known-good production commit.
- [ ] The Vercel preview still returns `setup-required` because
  only production has configured credentials.
- [ ] The encrypted weekly backup has **not run with real secrets**;
  a real backup, successful isolated restore, and four-week retention
  cannot be claimed operational before verifying them.
- [ ] Do not expose real learner data until backup, recovery and erasure
  restoration requirements are verified.

## Live backup prerequisite test — 2026-11-07 18:46 UTC

- [x] Installed `scripts/backup_crypto.py` and `.github/workflows/weekly-backup.yml`
  on `main` without merging the v2 application; the legacy site remains functional.
- [x] Triggered a one-time backup prerequisite check via a workflow-only push.
- [ ] GitHub Actions run
  [42402912049](https://github.com/mourad-sat/noor-alamal-python-reports/actions/runs/42402912049)
  **FAILED because `NOOR_BACKUP_DATABASE_URL` is not configured.**
  The job stopped before checking `NOOR_BACKUP_FERNET_KEY`, which is also required.
  No backup artifact was produced, so restoration/retention cannot be declared ready.
- [ ] The user must set both repository secrets in GitHub Actions without sharing
  their values in chat. Then rerun by a workflow-file-only push or manually dispatch.
- [ ] **Do not merge PR #1 or promote v2** until a genuine production backup exists
  and can be restored in an isolated database.

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
- [ ] Complete operational legal erasure assurance: app-level erasure and shared-evidence blocker now exist, but the encrypted-backup rotation and restoration-scrub procedure still require verification.
- [x] Confirm official logo appears on login, dashboards, paper notices and approved documents.
- [ ] Confirm the bulk grade export creates an acceptable **PDF** document, and review
  whether a downloadable server-generated PDF/ZIP is required beyond browser Print → PDF.
- [ ] Verify coordinator-only attachment selection is reflected in aggregate report output.
- [x] Add-only production schema migration applied to empty historical tables and RLS verified; full backup recovery point still pending.
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
