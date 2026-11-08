-- Private employee-entered professional card details.
-- Apply before deploying the code that registers the StaffProfile ORM model.
-- Sensitive information is limited to optional work contact details and a portrait.
BEGIN;

CREATE TABLE IF NOT EXISTS noor_app.v2_staff_profiles (
    staff_id INTEGER PRIMARY KEY
        REFERENCES noor_app.v2_staff(id) ON DELETE CASCADE,
    specialty VARCHAR(100) NOT NULL DEFAULT '',
    work_phone VARCHAR(35) NOT NULL DEFAULT '',
    work_email VARCHAR(150) NOT NULL DEFAULT '',
    photo_data BYTEA,
    photo_mime VARCHAR(30) NOT NULL DEFAULT '',
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL
        DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')
);

ALTER TABLE noor_app.v2_staff_profiles ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE noor_app.v2_staff_profiles FROM PUBLIC, anon, authenticated;
GRANT USAGE ON SCHEMA noor_app TO noor_reports_web;
GRANT SELECT, INSERT, UPDATE, DELETE
  ON TABLE noor_app.v2_staff_profiles TO noor_reports_web;
CREATE POLICY web_role_v2_staff_profiles ON noor_app.v2_staff_profiles
  FOR ALL TO noor_reports_web USING (true) WITH CHECK (true);

COMMIT;
