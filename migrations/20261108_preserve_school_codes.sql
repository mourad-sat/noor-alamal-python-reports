-- Preserve the official code contained in untouched school learner Excel exports.
-- Add-only migration: all existing learner rows retain a blank code until assigned.
BEGIN;
ALTER TABLE noor_app.v2_learners
  ADD COLUMN IF NOT EXISTS source_code VARCHAR(60) NOT NULL DEFAULT '';
COMMIT;
