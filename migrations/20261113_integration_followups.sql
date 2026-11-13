-- Add-only confidential follow-up register; existing enrollment and grade data untouched.
BEGIN;
CREATE TABLE IF NOT EXISTS noor_app.v2_integration_followups (
    id SERIAL PRIMARY KEY,
    enrollment_id INTEGER NOT NULL REFERENCES noor_app.v2_enrollments(id),
    outcome VARCHAR(30) NOT NULL
      CHECK (outcome IN ('continued_study','vocational_training','employed',
                         'seeking','needs_support','unknown')),
    observed_on DATE NOT NULL,
    organization VARCHAR(180) NOT NULL DEFAULT '',
    pathway_details VARCHAR(300) NOT NULL DEFAULT '',
    intervention VARCHAR(500) NOT NULL DEFAULT '',
    next_followup_on DATE,
    author_id INTEGER REFERENCES noor_app.v2_staff(id),
    recorded_by VARCHAR(140) NOT NULL,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL
      DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC'),
    CONSTRAINT ck_v2_integration_next_date
      CHECK (next_followup_on IS NULL OR next_followup_on >= observed_on)
);
CREATE INDEX IF NOT EXISTS ix_v2_integration_followups_enrollment_id
  ON noor_app.v2_integration_followups(enrollment_id);
ALTER TABLE noor_app.v2_integration_followups ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE noor_app.v2_integration_followups FROM PUBLIC, anon, authenticated;
GRANT USAGE ON SCHEMA noor_app TO noor_reports_web;
GRANT SELECT, INSERT, UPDATE, DELETE ON noor_app.v2_integration_followups TO noor_reports_web;
CREATE POLICY web_role_v2_integration_followups
  ON noor_app.v2_integration_followups FOR ALL TO noor_reports_web
  USING (true) WITH CHECK (true);
GRANT USAGE, SELECT ON SEQUENCE noor_app.v2_integration_followups_id_seq TO noor_reports_web;
COMMIT;
