-- Add-only competency rubric evaluations; historical learner/grade records remain untouched.
-- Apply before shipping the FastAPI module using this table.
BEGIN;

CREATE TABLE IF NOT EXISTS noor_app.v2_skill_evaluations (
    id SERIAL PRIMARY KEY,
    enrollment_id INTEGER NOT NULL REFERENCES noor_app.v2_enrollments(id),
    skill_key VARCHAR(45) NOT NULL,
    stage VARCHAR(24) NOT NULL,
    level INTEGER NOT NULL CHECK (level BETWEEN 1 AND 4),
    evaluated_on DATE NOT NULL,
    comment VARCHAR(600) NOT NULL DEFAULT '',
    author_id INTEGER NULL REFERENCES noor_app.v2_staff(id),
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL
        DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC'),
    CONSTRAINT uq_v2_skill_enrollment_key_stage
        UNIQUE (enrollment_id, skill_key, stage),
    CONSTRAINT ck_v2_skill_stage
        CHECK (stage IN ('initial', 'mid', 'final'))
);

CREATE INDEX IF NOT EXISTS ix_v2_skill_evaluations_enrollment_id
    ON noor_app.v2_skill_evaluations(enrollment_id);

ALTER TABLE noor_app.v2_skill_evaluations ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE noor_app.v2_skill_evaluations FROM PUBLIC, anon, authenticated;
GRANT USAGE ON SCHEMA noor_app TO noor_reports_web;
GRANT SELECT, INSERT, UPDATE, DELETE
    ON TABLE noor_app.v2_skill_evaluations TO noor_reports_web;
CREATE POLICY web_role_v2_skill_evaluations
    ON noor_app.v2_skill_evaluations FOR ALL
    TO noor_reports_web USING (true) WITH CHECK (true);
GRANT USAGE, SELECT ON SEQUENCE noor_app.v2_skill_evaluations_id_seq
    TO noor_reports_web;

COMMIT;
