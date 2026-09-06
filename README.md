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

The caregiver onboarding flow can check a household before requesting caregiver
credentials:

```json
POST /api/v1/auth/household/validate
{"household_code":"4V8F-29HC"}
```

Lowercase input, surrounding whitespace, and an omitted hyphen are normalized.
An active, non-archived household returns only `{"valid":true,"household_name":"…"}`.
Unknown, inactive, or archived households share the same generic 404 response.
This public endpoint neither authenticates a user nor issues a token, and caregiver
login independently rechecks the household, credentials, account status, role,
ownership or active assignment, and authorization. Submitted household codes are
not logged. The application has no rate-limit middleware, so deployments must
apply rate limiting to this unauthenticated endpoint at the edge/API gateway.

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
The title identifies severity and alert (for example, “Critical: High Heart Rate”);
the body shows the patient display name and reading (for example,
“Alera Test Patient • 154 BPM”). Missing display fields use the generic title
“Alera health alert” or body “A new alert needs your attention.”
Data contains only `type=ALERT`, `alert_id`, and `patient_id`.

Delivery is synchronous, best effort after commit, with no durable queue or retry
worker in this milestone. Requests can wait for delivery timeouts; a process crash
after commit can lose a push. Disabled/missing/invalid configuration and delivery
failures leave persisted alerts intact. Definitively unregistered/invalid FCM
registrations are removed; generic HTTP 400 and transient errors retain them.
Logs omit tokens, credentials, exception details and FCM response bodies.
Tests mock OAuth/FCM transport and never use service-account credentials.

## Development-only deployed push demo

`scripts/trigger_push_demo.py` uses HTTPS API calls only. It logs in to the fixed
fixture household `33333333-3333-3333-3333-333333333333` (`4V8F-29HC`), requires
exactly one accessible ACTIVE HR_HIGH alert for fixture patient
`a076ecdb-ae38-4f84-b490-e714977027ee` (Alera Test Patient), resolves it through the
alert API, and ingests a fresh current-UTC 154 BPM HEART_RATE event. Each run uses
a unique event ID, separate from the seed IDs. It polls for a different ACTIVE
HR_HIGH alert and verifies its triggering event. No database access, local
Firebase credentials, or debug endpoint is used. `--confirm-demo` is mandatory;
there are no patient or household overrides. Use only the development demo
deployment and run one demo command at a time.

Prerequisites: the explicit auth-development fixture and its demo alert seed
already exist on that deployment; the demo caregiver has an active assignment
to that patient; the phone is signed in as that caregiver, has notification
permission, and has registered its current FCM token. Configure FCM on Render as
above. Wait until any future-dated seed readings are in the past before running.

From the repository root on Fedora, with the project `.venv` dependencies
installed, paste this Bash command. Enter the HTTPS origin (without `/api/v1`)
and credentials at the prompts; prompt input is not stored in shell history,
and the password is hidden. The subshell clears the environment on exit and
turns off shell tracing before reading credentials:

```bash
(
  set +x
  read -r -p 'Deployed demo HTTPS origin: ' ALERA_DEMO_BASE_URL
  read -r -p 'Demo caregiver email: ' ALERA_DEMO_CAREGIVER_EMAIL
  read -r -s -p 'Demo caregiver password: ' ALERA_DEMO_CAREGIVER_PASSWORD
  printf '\n'
  export ALERA_DEMO_BASE_URL ALERA_DEMO_CAREGIVER_EMAIL ALERA_DEMO_CAREGIVER_PASSWORD
  .venv/bin/python -m scripts.trigger_push_demo --confirm-demo
)
```

Success prints only the new alert ID, ACTIVE status, HR_HIGH condition, and a
phone/Render-log reminder. Alert creation does not prove phone delivery: FCM
runs inside Render after commit and is best effort. Check the phone and Render
logs to complete the end-to-end check. Failures exit nonzero without printing
response bodies or credentials. A timeout may occur after a mutation committed;
inspect fixture state before retrying. If no ACTIVE fixture alert exists, the
command fails without ingesting an event; restore the development fixture using
the existing setup workflow. Re-running the idempotent seed alone does not
reopen a resolved alert.

Mocked HTTP tests (no deployed calls):

```bash
.venv/bin/pytest tests/unit/test_trigger_push_demo.py -q
```

## Caregiver-created patients

### Caregiver patient reads

The authenticated Flutter People and Patient Detail views use:

```text
GET /api/v1/patients?limit=20&offset=0&search=name
GET /api/v1/patients/{patient_id}
```

Both require the existing bearer token. Caregivers see only non-archived patients
for whom they have a current assignment; sharing a household is insufficient.
Care admins see assigned and unassigned non-archived patients in active households
they own. Elderly-patient actors receive 403, and patient detail returns 404 for
records outside the actor's scope. The list is ordered by full name and then
patient ID, and returns an authorization-consistent total before
pagination (`limit` defaults to 20 and is capped at 100).

Both responses include `current_summary`. Latest heart-rate and SpO2 readings use
accepted (`VALID_REALTIME` or `DELAYED_USABLE`) events and event time, with stable
tie-breaking; invalid or older delayed readings do not replace newer readings.
`last_check_in` is the newest returned HR/SpO2 event time. Alert counts include
the alert API's unresolved `ACTIVE` and `ACKNOWLEDGED` statuses. Monitoring is
`CRITICAL` when any unresolved Critical alert exists, otherwise `WARNING` when
any unresolved Warning exists, `STABLE` when accepted HR/SpO2 data exists, and
`NO_DATA` otherwise. Device connection and sync values come directly from the
existing patient integration status and last-sync fields; no readings are inferred.
Reads use a fixed set of page, summary, alert, and assignment queries and do not
mutate health events, trackers, or alerts.

### Patient creation

Authenticated caregivers and care admins can call `POST /api/v1/patients` with
`Authorization: Bearer <access token>`. The household comes exclusively from
the JWT. Current active household membership (caregiver assignment) or ownership
(care admin) is rechecked. Caregivers receive an active assignment to the new
patient; admin-created patients remain unassigned.

Example JSON:

```json
{
  "full_name": "New Patient",
  "birthdate": "1950-01-02",
  "sex": "FEMALE",
  "address_or_room": "Room 2",
  "baseline_heart_rate": 72,
  "baseline_spo2": 98,
  "monitoring_notes": "Caregiver notes"
}
```

Only `full_name` is required. Optional fields also include `phone_number`
(existing 11-character user-field limit), `emergency_contact_name`,
`emergency_contact_phone`, `known_conditions`, and `medications`. Conditions,
medications, and notes are strings. Sex accepts MALE, FEMALE, or OTHER. Baseline
readings are optional profile values, not rule thresholds; existing threshold
defaults remain unchanged. Unknown fields, including household or assignment
overrides, are rejected.

The 201 response returns the profile, patient/user/household IDs, account status,
archive timestamp, creation timestamp, and `assignment` (the existing assignment
response shape, or null). Creation is atomic and requires no patient email or
password. It does not issue an access code. Use the existing explicit
`POST /api/v1/patients/{patient_id}/access-codes` action afterward, then the
existing patient access login flow.

## Patient access codes

Patient access codes are one-time, globally usable credentials in the canonical
format `XXXX-XXXX-XXXX`. They contain 12 random uppercase characters and omit
visually ambiguous `0`, `O`, `1`, `I`, and `L`. Caregivers issue them through
the existing `POST /api/v1/patients/{patient_id}/access-codes` endpoint; its
authorization, request, and 201 response shape are unchanged. The plaintext
code appears only in that response.

Patients authenticate without a household code:

```json
POST /api/v1/auth/patient/access
{"access_code":"7K3M-9Q2D-R8TX"}
```

Lowercase input, surrounding whitespace, and omitted hyphens are accepted.
The service normalizes to the canonical form, uses a four-character selector to
find a small set of eligible salted-scrypt hashes, and verifies the whole code
before resolving the patient household. It returns the existing patient bearer
response and claims. Invalid, expired, revoked, consumed, malformed, or
unavailable credentials receive the same generic 401 response. The service does
not log submitted codes. Deployments should apply standard edge/API rate limiting
to this unauthenticated endpoint; Alera does not add a separate rate-limit
framework here.

Migration `c8d4e52f6b91` adds the nullable selector and active-code lookup index.
It revokes every previously unconsumed legacy code because its old format cannot
be globally resolved, while retaining every record for audit and leaving patient
data and existing JWTs untouched. Run `alembic upgrade head`.

Deploy with `alembic upgrade head`. Migration `f3a9120bc651` preserves existing
rows, allows unknown birthdate/sex, and adds nullable profile columns. Its
downgrade refuses rows with missing demographics or populated new profile fields
rather than inventing demographics or silently dropping profile data.
