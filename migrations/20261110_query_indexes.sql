-- Add-only indexes matching SQLAlchemy's generated index names.
-- Staging review required before applying to production; no data or permission changes.
-- Do not run during peak usage without assessing table locks and existing indexes.
BEGIN;
CREATE INDEX IF NOT EXISTS ix_v2_enrollments_track_id ON noor_app.v2_enrollments (track_id);
CREATE INDEX IF NOT EXISTS ix_v2_enrollments_center_id ON noor_app.v2_enrollments (center_id);
CREATE INDEX IF NOT EXISTS ix_v2_attendance_enrollment_id ON noor_app.v2_attendance (enrollment_id);
CREATE INDEX IF NOT EXISTS ix_v2_slots_center_id ON noor_app.v2_slots (center_id);
CREATE INDEX IF NOT EXISTS ix_v2_slots_track_id ON noor_app.v2_slots (track_id);
CREATE INDEX IF NOT EXISTS ix_v2_slots_staff_id ON noor_app.v2_slots (staff_id);
CREATE INDEX IF NOT EXISTS ix_v2_discipline_enrollment_id ON noor_app.v2_discipline (enrollment_id);
CREATE INDEX IF NOT EXISTS ix_v2_notices_enrollment_id ON noor_app.v2_notices (enrollment_id);
CREATE INDEX IF NOT EXISTS ix_v2_permits_learner_id ON noor_app.v2_permits (learner_id);
COMMIT;
