# Patient access enrollment

`POST /api/v1/auth/patient/access` accepts:

```json
{"household_code": "<HOUSEHOLD_CODE>", "access_code": "<ONE_TIME_ACCESS_CODE>"}
```

Success (200) uses the caregiver login response shape: `access_token`,
`token_type: "bearer"`, `expires_at`, and `actor` (user ID, name, role
`ELDERLY_PATIENT`, household ID/name/code). No access-code record or hash is
returned. Codes are trimmed and uppercased. Invalid household/code, expired,
used or revoked code, wrong household, archived patient, or inactive account
returns 401 with `{"detail":"Invalid household code or access code."}` and
`WWW-Authenticate: Bearer`. Malformed request bodies still use standard 422
validation. An atomic conditional database update consumes the code; concurrent
redemptions have exactly one winner. Signing or transaction failure rolls back
consumption. No migration is needed: `used_at`, `revoked_at`, and `expires_at`
already exist.

The shared HS256 JWT has `sub` (user ID), `household_id`, `iss`, `iat`, and `exp`.
Role is resolved from the database, not supplied by the client. Every bearer
request checks ACTIVE user status. Patient sessions additionally require a
non-archived patient in the claimed active, non-archived household. Caregiver
alert routes explicitly reject patients with 403. Lifetime uses
`ALERA_JWT_ACCESS_TOKEN_MINUTES` (default 60 minutes); no refresh session was
added. Flutter should securely persist this bearer token and honor its expiry;
a new caregiver-issued code is required to sign in again after expiry.

Patient-code issue/revoke endpoints now require bearer authentication. CARE_ADMIN
must own the active household; CAREGIVER must have an active assignment to the
non-archived patient; patients are denied. Existing code issuance revokes unused
codes. Assignment/unassignment administration retains its Phase 9A behavior.
Revoking/resetting an enrollment code does not revoke an already-issued JWT.

## Explicit development demo issuance

The patient `a076ecdb-ae38-4f84-b490-e714977027ee` and its active household owner
must already exist. Run only against your local development database:

```bash
ENVIRONMENT=development SQL_ECHO=false DATABASE_URL='<LOCAL_DEVELOPMENT_DATABASE_URL>' .venv/bin/python -m scripts.demo_patient_access --output '<NEW_PRIVATE_CODE_FILE>'
```

If any code has previously been issued (including used/expired codes), explicitly
reset to revoke unused codes and issue a fresh 24-hour, one-time code:

```bash
ENVIRONMENT=development SQL_ECHO=false DATABASE_URL='<LOCAL_DEVELOPMENT_DATABASE_URL>' .venv/bin/python -m scripts.demo_patient_access --reset --output '<NEW_PRIVATE_CODE_FILE>'
```

The file must not exist and is created with mode 0600. The CLI never prints the
code or exposes an HTTP endpoint, requires explicit `ENVIRONMENT=development`,
and is never called on startup. Read the private file locally to enter the code.

## Validation commands

Use an isolated database whose name contains `test`; tests truncate its tables.

```bash
TEST_DATABASE_URL='<ISOLATED_POSTGRES_TEST_DATABASE_URL>' .venv/bin/pytest tests/integration/test_patient_auth.py tests/integration/test_household_access_api.py tests/integration/test_caregiver_auth.py tests/integration/test_alert_api.py -q
TEST_DATABASE_URL='<ISOLATED_POSTGRES_TEST_DATABASE_URL>' .venv/bin/pytest -q
.venv/bin/python -m compileall -q app scripts tests
git diff --check
```
