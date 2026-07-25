# Alera Backend

## Test databases

Database-free tests run without `DATABASE_URL`. PostgreSQL integration tests require
an isolated `TEST_DATABASE_URL`; as a safety guard, its database name must contain
`test` and it must not equal `DATABASE_URL`. The integration suite migrates and
truncates only that test database.

```bash
pytest -v
TEST_DATABASE_URL=postgresql+psycopg://.../alera_test pytest -m integration -v
```

The migration that adds `HR_NORMAL` and `SPO2_NORMAL` is partially irreversible:
its downgrade retains those PostgreSQL enum values because removing enum members is
unsafe. Migration tests verify that documented behavior.

## Phase 5A alert foundation

Alert records are caregiver-facing cases. An alert's status describes its
caregiver handling state; `RESOLVED` means the caregiver considers the case
handled and does not by itself prove physiological recovery. Alert status does
not control `ConditionTracker.active`.

Alert creation and notification delivery remain separate concerns.
Notification, aggregation, cooldown, and suppression behavior is deferred to
later Phase 5 work.

## Deferred security work

Health-event ingestion does not yet authenticate or authorize its source. Future
work must authenticate devices/caregivers, enforce device/caregiver-to-patient
authorization, define archived/disabled patient behavior, and test unauthorized
patient submissions before the endpoint is treated as production-secure.

`usual_spo2_max` is retained as a baseline/trend field. Current alert evaluation
uses `usual_spo2_min`; the maximum is constrained for data integrity but does not
change current SpO₂ classification behavior.
