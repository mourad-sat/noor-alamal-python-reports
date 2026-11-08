-- Private learner portrait storage. No public access; application enforces
-- staff role and center authorization before upload, retrieval, or print.
-- Add only; existing learner records remain unchanged.
BEGIN;

CREATE TABLE IF NOT EXISTS noor_app.v2_learner_photos (
    learner_id INTEGER PRIMARY KEY REFERENCES noor_app.v2_learners(id),
    content BYTEA NOT NULL,
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL
        DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')
);

ALTER TABLE noor_app.v2_learner_photos ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE noor_app.v2_learner_photos FROM PUBLIC, anon, authenticated;
GRANT USAGE ON SCHEMA noor_app TO noor_reports_web;
GRANT SELECT, INSERT, UPDATE, DELETE
  ON TABLE noor_app.v2_learner_photos TO noor_reports_web;
CREATE POLICY web_role_v2_learner_photos ON noor_app.v2_learner_photos
    FOR ALL TO noor_reports_web USING (true) WITH CHECK (true);

COMMIT;
