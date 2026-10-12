# Patient archive integration

POST `/api/v1/patients/{patient_id}/archive`

Use the existing caregiver Bearer token. No request body is required.
Success: HTTP 200, JSON:

```json
{"patient_id":"patient UUID", "archived_at":"ISO-8601 timestamp with timezone"}
```

Assigned caregivers and the household's owning care administrator can archive,
using the same scope as profile editing. Archiving affects all caregivers of that
patient. This is a soft archive: profile, assignments and historical records are
preserved. Patient lists and detail reads exclude archived patients. Existing
patient tokens fail authentication and unused access codes are revoked. A retry
by an actor still in scope returns 200 and the original timestamp.

Errors: 401 invalid/missing token; 403 wrong actor role; 404 missing patient or
outside actor scope; 422 invalid UUID. Errors use FastAPI's `detail` field.
Only remove the patient from frontend state after HTTP 200. Keep the dialog open
on failure. No database migration is needed: `archived_at` already exists.

Monitoring defaults: PATCH `/api/v1/patients/{patient_id}/monitoring-settings`
with `normal_hr_min: 60`, `normal_hr_max: 100`, `usual_spo2_min: 95`, and
`usual_spo2_max: null` returns `threshold_mode: "DEFAULT"`. Explicitly send null
to clear an existing custom SpO2 maximum.
