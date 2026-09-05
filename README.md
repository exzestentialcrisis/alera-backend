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

## Caregiver authentication and demo seed

Caregivers and care admins authenticate within a household using
`POST /api/v1/auth/caregiver/login`. Configure `ALERA_JWT_SECRET` with a long,
random secret and set `ALERA_JWT_ACCESS_TOKEN_MINUTES` to the desired positive
token lifetime.

To create or refresh the developer demo caregiver and ensure its active patient
assignment exists, configure the three demo variables and run:

```bash
ALERA_DEMO_CAREGIVER_EMAIL=caregiver@example.com \
ALERA_DEMO_CAREGIVER_PASSWORD='replace-with-demo-password' \
ALERA_DEMO_PATIENT_ID='replace-with-demo-patient-uuid' \
python -m app.auth.seed_demo_caregiver
```

The command is idempotent and prints only the caregiver email and resolved
household name/code. It obtains database connectivity from the standard
`DATABASE_URL` setting.

## Phase 5A alert foundation

Alert records are caregiver-facing cases. An alert's status describes its
caregiver handling state; `RESOLVED` means the caregiver considers the case
handled and does not by itself prove physiological recovery. Alert status does
not control `ConditionTracker.active`.

Alert creation and notification delivery remain separate concerns.
Optional post-commit push delivery is documented below. Aggregation, cooldown,
and suppression behavior is deferred to later Phase 5 work.

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

Raw sensor callbacks are not expected to be stored individually.

## Caregiver Alert API MVP

The development caregiver API exposes:

```text
GET  /api/v1/alerts
GET  /api/v1/alerts/{alert_id}
GET  /api/v1/alerts/{alert_id}/actions
POST /api/v1/alerts/{alert_id}/acknowledge
POST /api/v1/alerts/{alert_id}/resolve
POST /api/v1/alerts/{alert_id}/false-alarm
POST /api/v1/alerts/{alert_id}/notes
POST /api/v1/alerts/{alert_id}/interventions
```

Every alert route requires a bearer access token issued by the caregiver login
endpoint. Results are scoped to actively assigned patients for caregivers and
owned households for care admins:

```bash
curl -X POST http://localhost:8000/api/v1/alerts/ALERT_UUID/acknowledge \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer ACCESS_TOKEN' \
  -d '{"note":"I am reviewing this alert."}'
```

The temporary `X-Alera-Actor-Id` header remains only on the Phase 9A household
access administration endpoints; it is not accepted as authority by alert routes.

Lifecycle transitions are `ACTIVE → ACKNOWLEDGED → RESOLVED`, with either
`ACTIVE` or `ACKNOWLEDGED` also permitted to become `FALSE_ALARM`. Repeating an
already-completed lifecycle transition is idempotent. Notes and interventions
do not change status, and caregiver resolution does not change physiological
condition trackers.

The Caregiver App can poll nonterminal alerts with pagination:

```bash
curl 'http://localhost:8000/api/v1/alerts?status=ACTIVE&status=ACKNOWLEDGED&limit=20&offset=0' \
  -H 'Authorization: Bearer ACCESS_TOKEN'
```

Action responses contain the updated alert, the new action (or `null` for an
idempotent repeat), and an `idempotent` flag:

```json
{
  "alert": {"alert_id": "…", "status": "ACKNOWLEDGED", "severity": "CRITICAL"},
  "action": {"action_type": "ACKNOWLEDGE", "action_note": "Reviewing"},
  "idempotent": false
}
```

## Deferred security work

Health-event ingestion does not yet authenticate or authorize its source. Future
work must authenticate devices/caregivers, enforce device/caregiver-to-patient
authorization, define archived/disabled patient behavior, and test unauthorized
patient submissions before the endpoint is treated as production-secure.

`usual_spo2_max` is retained as a baseline/trend field. Current alert evaluation
uses `usual_spo2_min`; the maximum is constrained for data integrity but does not
change current SpO₂ classification behavior.


## Caregiver push notifications (optional)

Run `alembic upgrade head` before deploying the device endpoints. Authenticated
caregivers/care admins can register Android devices with
`POST /api/v1/devices/fcm-token` and JSON
`{"token": "<FCM registration token>", "platform": "ANDROID"}`. Registration is
idempotent and reassigns an existing token to the authenticated user. Refresh it
on sign-in/token refresh; use `DELETE /api/v1/devices/fcm-token` with
`{"token": "<FCM registration token>"}` before sign-out. Delete only affects the
actor's registration. Both return `{"status": "ok"}`. Tokens accept 1–2048 ASCII
letters, digits, underscores, colons, periods and hyphens; validation responses
never echo input. SQL parameters are hidden even with `SQL_ECHO=true`.

Set these environment variables on the Render backend service:

- `FCM_ENABLED=true` (defaults to `false`; keep disabled in local/test environments).
- `FIREBASE_PROJECT_ID`: the target Firebase project ID.
- `FIREBASE_SERVICE_ACCOUNT_JSON`: the complete service-account JSON as a secret
  environment value, never a committed file or a client-side setting.

Enable the Firebase Cloud Messaging API (HTTP v1) in the target project and grant
the service account permission to send messages (`cloudmessaging.messages.create`,
e.g. Firebase Cloud Messaging API Admin). The sender uses Google's service-account
OAuth flow with the `firebase.messaging` scope and bounded HTTP timeouts. See
[Firebase HTTP v1 setup](https://firebase.google.com/docs/cloud-messaging/send/v1-api).

Only a new ACTIVE alert from the existing rule flow queues delivery. The outer
transaction must commit before a separate session selects devices belonging to
active caregivers/care admins with a current patient assignment. Alert reads,
updates, reused alerts and rolled-back transactions do not trigger sends.
The title is “Alera health alert”; the body is “A new alert needs your attention.”
Data contains only `type=ALERT`, `alert_id`, and `patient_id`.

Delivery is synchronous, best effort after commit, with no durable queue or retry
worker in this milestone. Requests can wait for delivery timeouts; a process crash
after commit can lose a push. Disabled/missing/invalid configuration and delivery
failures leave persisted alerts intact. Definitively unregistered/invalid FCM
registrations are removed; generic HTTP 400 and transient errors retain them.
Logs omit tokens, credentials, exception details and FCM response bodies.
Tests mock OAuth/FCM transport and never use service-account credentials.
