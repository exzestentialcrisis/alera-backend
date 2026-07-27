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

## Phase 5 alert behavior

A `VALID_REALTIME` heart-rate reading above 150 bpm or SpO₂ reading below
90% produces a Critical evaluation and an ACTIVE Critical alert in the same
transaction. For example, submitting a heart-rate value of `151` through the
health-event ingestion pipeline should create an `HR_HIGH` alert and link the
event evaluation to it.

Heart-rate Warning alerts require five elapsed minutes of continuously abnormal
accepted real-time readings. Gaps up to and including 90 seconds preserve the
occurrence, including a planned approximately 30-second sensor interruption;
larger gaps restart its timer. Persistence uses event timestamps, and duration
alone never escalates a Warning to Critical.

For SpO₂, values below 90% create an immediate Critical alert, values from 90%
through 93% are Warning candidates, and values of 94% or above are normal. Two
consecutive accepted Warning candidates qualify a Warning alert. Gaps up to and
including five minutes preserve the consecutive occurrence; larger gaps restart
the count. Duration alone never escalates an SpO₂ Warning to Critical.

Raw sensor callbacks are not expected to be stored individually. Notification
delivery remains deferred.

## Deferred security work

Health-event ingestion does not yet authenticate or authorize its source. Future
work must authenticate devices/caregivers, enforce device/caregiver-to-patient
authorization, define archived/disabled patient behavior, and test unauthorized
patient submissions before the endpoint is treated as production-secure.

`usual_spo2_max` is retained as a baseline/trend field. Current alert evaluation
uses `usual_spo2_min`; the maximum is constrained for data integrity but does not
change current SpO₂ classification behavior.
