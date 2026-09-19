from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import event

from app.alerts.model import Alert, AlertStatus
from app.condition_trackers.model import ConditionTracker
from app.event_evaluations.model import ConditionKey, EvaluationSeverity
from app.health_events.model import HealthEvent, MetricType, ValidationStatus
from app.core.time import utc_now
from app.household_access.model import CaregiverPatientAssignment, PatientAccessCode
from app.households.model import HouseholdStatus
from app.patients.model import ElderlyPatient, Sex
from app.users.model import AccountStatus, User, UserRole
from tests.integration.test_caregiver_auth import api_app, make_household, request
from tests.integration.test_patient_creation import headers

pytestmark = pytest.mark.integration
NOW = datetime(2026, 9, 6, 4, tzinfo=timezone.utc)


def add_patient(db, household, name, *, archived=False):
    user = User(
        full_name=name,
        phone_number="09123456789",
        role=UserRole.ELDERLY_PATIENT,
    )
    db.add(user)
    db.flush()
    patient = ElderlyPatient(
        user_id=user.user_id,
        household_id=household.household_id,
        birthdate=datetime(1950, 1, 2).date(),
        sex=Sex.FEMALE,
        address_or_room="Room 2",
        emergency_contact_name="Family",
        emergency_contact_phone="09170000000",
        known_conditions="Hypertension",
        medications="Medication",
        baseline_heart_rate=Decimal("72.50"),
        baseline_spo2=Decimal("98.00"),
        health_notes="Check twice daily",
        archived_at=NOW if archived else None,
    )
    db.add(patient)
    db.flush()
    return user, patient


def assign(db, caregiver, patient, admin, *, ended=False):
    assignment = CaregiverPatientAssignment(
        caregiver_user_id=caregiver.user_id,
        patient_id=patient.patient_id,
        assigned_by_user_id=admin.user_id,
        unassigned_at=NOW if ended else None,
    )
    db.add(assignment)
    db.flush()
    return assignment


def add_access_code(
    db,
    patient,
    admin,
    *,
    created_at=None,
    expires_at=None,
    used_at=None,
    revoked_at=None,
):
    now = utc_now()
    code = PatientAccessCode(
        patient_id=patient.patient_id,
        code_hash="not-a-returnable-code-hash",
        access_code_selector="SAFE",
        created_by_user_id=admin.user_id,
        created_at=created_at or now,
        expires_at=expires_at or now + timedelta(hours=1),
        used_at=used_at,
        revoked_at=revoked_at,
    )
    db.add(code)
    db.flush()
    return code


def add_event(
    db,
    patient,
    metric,
    value,
    recorded_at,
    *,
    status=ValidationStatus.VALID_REALTIME,
    received_at=None,
):
    health_event = HealthEvent(
        patient_id=patient.patient_id,
        metric_type=metric,
        numeric_value=value,
        metric_unit="bpm" if metric is MetricType.HEART_RATE else "%",
        recorded_at=recorded_at,
        received_at=received_at or recorded_at,
        validation_status=status,
        validation_reason="invalid" if status is ValidationStatus.INVALID else None,
        raw_payload={},
    )
    db.add(health_event)
    db.flush()
    return health_event


def add_alert(db, patient, severity, *, status=AlertStatus.ACTIVE):
    alert = Alert(
        patient_id=patient.patient_id,
        condition_key=(
            ConditionKey.HR_HIGH
            if severity is EvaluationSeverity.CRITICAL
            else ConditionKey.SPO2_LOW
        ),
        severity=severity,
        status=status,
        detected_at=NOW,
        confirmed_at=NOW,
        resolved_at=NOW + timedelta(minutes=1)
        if status is AlertStatus.RESOLVED
        else None,
    )
    db.add(alert)
    db.flush()
    return alert


def setup_scope(db):
    admin, household, original, patient_user = make_household(
        db, "Home", "AAAA-BBBB"
    )
    patient_user.full_name = "Bravo Patient"
    caregiver = User(full_name="Caregiver", role=UserRole.CAREGIVER)
    other = User(full_name="Other Caregiver", role=UserRole.CAREGIVER)
    db.add_all([caregiver, other])
    db.flush()
    active_assignment = assign(db, caregiver, original, admin)
    _, unassigned = add_patient(db, household, "Alpha Unassigned")
    _, historical = add_patient(db, household, "Charlie Historical")
    assign(db, caregiver, historical, admin, ended=True)
    _, archived = add_patient(db, household, "Delta Archived", archived=True)
    db.commit()
    return {
        "admin": admin,
        "household": household,
        "caregiver": caregiver,
        "other": other,
        "assigned": original,
        "unassigned": unassigned,
        "historical": historical,
        "archived": archived,
        "assignment": active_assignment,
    }


def get_list(app, actor, household, query=""):
    return request(
        app,
        "GET",
        f"/api/v1/patients{query}",
        headers=headers(actor, household),
    )


def test_assignment_and_admin_scopes(api_app, db_session):
    scope = setup_scope(db_session)
    other_admin, other_household, other_patient, _ = make_household(
        db_session, "Other", "CCCC-DDDD"
    )
    assign(db_session, scope["other"], other_patient, other_admin)
    db_session.commit()

    caregiver = get_list(
        api_app, scope["caregiver"], scope["household"]
    ).json()
    assert [item["patient_id"] for item in caregiver["items"]] == [
        str(scope["assigned"].patient_id)
    ]
    assert caregiver["items"][0]["current_summary"]["monitoring_status"] == "NO_DATA"
    assert caregiver["items"][0]["current_summary"] == {
        "latest_heart_rate": None,
        "latest_spo2": None,
        "today_steps": None,
        "steps_updated_at": None,
        "latest_sleep_duration_seconds": None,
        "latest_sleep_date": None,
        "last_check_in": None,
        "active_alert_count": 0,
        "highest_active_alert_severity": None,
        "monitoring_status": "NO_DATA",
        "device_connection_status": "NOT_CONNECTED",
        "last_device_sync_at": None,
    }

    admin = get_list(api_app, scope["admin"], scope["household"]).json()
    assert [item["full_name"] for item in admin["items"]] == [
        "Alpha Unassigned",
        "Bravo Patient",
        "Charlie Historical",
    ]
    assert str(other_patient.patient_id) not in {
        item["patient_id"] for item in admin["items"]
    }


def test_inactive_household_is_excluded_for_both_roles(api_app, db_session):
    scope = setup_scope(db_session)
    scope["household"].household_status = HouseholdStatus.INACTIVE
    db_session.commit()
    assert get_list(api_app, scope["caregiver"], scope["household"]).json()["total"] == 0
    assert get_list(api_app, scope["admin"], scope["household"]).json()["total"] == 0


def test_detail_is_scoped_and_contains_full_safe_profile(api_app, db_session):
    scope = setup_scope(db_session)
    visible = request(
        api_app,
        "GET",
        f"/api/v1/patients/{scope['assigned'].patient_id}",
        headers=headers(scope["caregiver"], scope["household"]),
    )
    assert visible.status_code == 200
    body = visible.json()
    assert body["assignment"]["assignment_id"] == str(
        scope["assignment"].assignment_id
    )
    assert body["monitoring_notes"] is None
    forbidden_names = {
        "password_hash",
        "access_code_selector",
        "code_hash",
        "access_token",
        "email",
    }
    assert forbidden_names.isdisjoint(body)
    assert all(name not in visible.text for name in forbidden_names)
    assert request(
        api_app,
        "GET",
        f"/api/v1/patients/{scope['unassigned'].patient_id}",
        headers=headers(scope["caregiver"], scope["household"]),
    ).status_code == 404


def test_detail_patient_access_is_not_connected_for_new_patient(api_app, db_session):
    scope = setup_scope(db_session)

    body = request(
        api_app,
        "GET",
        f"/api/v1/patients/{scope['assigned'].patient_id}",
        headers=headers(scope["caregiver"], scope["household"]),
    ).json()

    assert body["patient_access"] == {
        "status": "NOT_CONNECTED",
        "pending_access_code_id": None,
        "pending_expires_at": None,
        "connected_at": None,
    }
    admin_body = request(
        api_app,
        "GET",
        f"/api/v1/patients/{scope['assigned'].patient_id}",
        headers=headers(scope["admin"], scope["household"]),
    ).json()
    assert admin_body["patient_access"] == body["patient_access"]


@pytest.mark.parametrize("state", ["expired", "revoked"])
def test_detail_patient_access_ignores_unusable_invitations(
    api_app, db_session, state
):
    scope = setup_scope(db_session)
    now = utc_now()
    add_access_code(
        db_session,
        scope["assigned"],
        scope["admin"],
        expires_at=now - timedelta(seconds=1) if state == "expired" else None,
        revoked_at=now if state == "revoked" else None,
    )
    db_session.commit()

    body = request(
        api_app,
        "GET",
        f"/api/v1/patients/{scope['assigned'].patient_id}",
        headers=headers(scope["caregiver"], scope["household"]),
    ).json()

    assert body["patient_access"] == {
        "status": "NOT_CONNECTED",
        "pending_access_code_id": None,
        "pending_expires_at": None,
        "connected_at": None,
    }


def test_detail_patient_access_returns_pending_invitation(api_app, db_session):
    scope = setup_scope(db_session)
    code = add_access_code(db_session, scope["assigned"], scope["admin"])
    db_session.commit()

    access = request(
        api_app,
        "GET",
        f"/api/v1/patients/{scope['assigned'].patient_id}",
        headers=headers(scope["caregiver"], scope["household"]),
    ).json()["patient_access"]

    assert access["status"] == "INVITE_PENDING"
    assert access["pending_access_code_id"] == str(code.access_code_id)
    assert datetime.fromisoformat(access["pending_expires_at"]) == code.expires_at
    assert access["connected_at"] is None


def test_detail_patient_access_connected_takes_precedence(api_app, db_session):
    scope = setup_scope(db_session)
    connected_at = utc_now() - timedelta(minutes=2)
    add_access_code(
        db_session, scope["assigned"], scope["admin"], used_at=connected_at
    )
    add_access_code(db_session, scope["assigned"], scope["admin"])
    db_session.commit()

    access = request(
        api_app,
        "GET",
        f"/api/v1/patients/{scope['assigned'].patient_id}",
        headers=headers(scope["caregiver"], scope["household"]),
    ).json()["patient_access"]

    assert access["status"] == "CONNECTED"
    assert access["pending_access_code_id"] is None
    assert access["pending_expires_at"] is None
    assert datetime.fromisoformat(access["connected_at"]) == connected_at


def test_detail_patient_access_uses_most_recent_redemption(api_app, db_session):
    scope = setup_scope(db_session)
    older = utc_now() - timedelta(minutes=2)
    newer = utc_now() - timedelta(minutes=1)
    add_access_code(db_session, scope["assigned"], scope["admin"], used_at=older)
    add_access_code(db_session, scope["assigned"], scope["admin"], used_at=newer)
    db_session.commit()

    access = request(
        api_app,
        "GET",
        f"/api/v1/patients/{scope['assigned'].patient_id}",
        headers=headers(scope["caregiver"], scope["household"]),
    ).json()["patient_access"]
    assert datetime.fromisoformat(access["connected_at"]) == newer


def test_detail_patient_access_selects_newest_usable_invitation(api_app, db_session):
    scope = setup_scope(db_session)
    created_at = utc_now() - timedelta(minutes=2)
    older = add_access_code(
        db_session, scope["assigned"], scope["admin"], created_at=created_at
    )
    newest = add_access_code(
        db_session,
        scope["assigned"],
        scope["admin"],
        created_at=created_at + timedelta(minutes=1),
    )
    db_session.commit()

    access = request(
        api_app,
        "GET",
        f"/api/v1/patients/{scope['assigned'].patient_id}",
        headers=headers(scope["caregiver"], scope["household"]),
    ).json()["patient_access"]
    assert access["pending_access_code_id"] == str(newest.access_code_id)
    assert access["pending_access_code_id"] != str(older.access_code_id)


def test_patient_access_details_do_not_expose_sensitive_code_fields(api_app, db_session):
    scope = setup_scope(db_session)
    code = add_access_code(db_session, scope["assigned"], scope["admin"])
    db_session.commit()

    body = request(
        api_app,
        "GET",
        f"/api/v1/patients/{scope['assigned'].patient_id}",
        headers=headers(scope["caregiver"], scope["household"]),
    ).json()

    assert body["patient_access"]["pending_access_code_id"] == str(code.access_code_id)
    assert {"code_hash", "access_code_selector", "created_by_user_id"}.isdisjoint(
        body["patient_access"]
    )
    assert "not-a-returnable-code-hash" not in str(body)


def test_patient_list_does_not_include_patient_access(api_app, db_session):
    scope = setup_scope(db_session)
    add_access_code(db_session, scope["assigned"], scope["admin"])
    db_session.commit()

    body = get_list(api_app, scope["caregiver"], scope["household"]).json()
    assert "patient_access" not in body["items"][0]


def test_patient_role_is_forbidden_and_disabled_actors_are_unauthorized(
    api_app, db_session
):
    scope = setup_scope(db_session)
    patient_user = db_session.get(User, scope["assigned"].user_id)
    assert get_list(api_app, patient_user, scope["household"]).status_code == 403

    for status_value in (AccountStatus.DISABLED, AccountStatus.ARCHIVED):
        scope["caregiver"].account_status = status_value
        db_session.commit()
        assert get_list(
            api_app, scope["caregiver"], scope["household"]
        ).status_code == 401
        scope["caregiver"].account_status = AccountStatus.ACTIVE
        db_session.commit()


def test_pagination_total_order_and_case_insensitive_search(api_app, db_session):
    scope = setup_scope(db_session)
    response = get_list(
        api_app,
        scope["admin"],
        scope["household"],
        "?limit=1&offset=1&search=A",
    )
    assert response.status_code == 200
    assert response.json()["total"] == 3
    assert response.json()["limit"] == 1
    assert response.json()["offset"] == 1
    assert [item["full_name"] for item in response.json()["items"]] == [
        "Bravo Patient"
    ]


def test_latest_readings_and_stable_summary(api_app, db_session):
    scope = setup_scope(db_session)
    patient = scope["assigned"]
    add_event(db_session, patient, MetricType.HEART_RATE, 70, NOW)
    add_event(db_session, patient, MetricType.HEART_RATE, 82, NOW + timedelta(minutes=2))
    add_event(db_session, patient, MetricType.SPO2, 97, NOW + timedelta(minutes=1))
    add_event(
        db_session,
        patient,
        MetricType.HEART_RATE,
        199,
        NOW + timedelta(minutes=3),
        status=ValidationStatus.INVALID,
    )
    add_event(
        db_session,
        patient,
        MetricType.SPO2,
        90,
        NOW - timedelta(days=1),
        received_at=NOW + timedelta(days=1),
    )
    db_session.commit()

    body = get_list(api_app, scope["caregiver"], scope["household"]).json()[
        "items"
    ][0]["current_summary"]
    assert body["latest_heart_rate"]["value"] == "82.00"
    assert body["latest_heart_rate"]["unit"] == "bpm"
    assert body["latest_spo2"]["value"] == "97.00"
    assert datetime.fromisoformat(body["last_check_in"]) == NOW + timedelta(minutes=2)
    assert body["monitoring_status"] == "STABLE"


@pytest.mark.parametrize(
    "severity, expected",
    [
        (EvaluationSeverity.WARNING, "WARNING"),
        (EvaluationSeverity.CRITICAL, "CRITICAL"),
    ],
)
def test_active_alert_summary(api_app, db_session, severity, expected):
    scope = setup_scope(db_session)
    patient = scope["assigned"]
    add_alert(db_session, patient, EvaluationSeverity.WARNING)
    if severity is EvaluationSeverity.CRITICAL:
        add_alert(db_session, patient, severity)
    add_alert(
        db_session,
        patient,
        EvaluationSeverity.CRITICAL,
        status=AlertStatus.RESOLVED,
    )
    db_session.commit()

    summary = get_list(api_app, scope["caregiver"], scope["household"]).json()[
        "items"
    ][0]["current_summary"]
    assert summary["active_alert_count"] == (
        2 if severity is EvaluationSeverity.CRITICAL else 1
    )
    assert summary["highest_active_alert_severity"] == severity.value
    assert summary["monitoring_status"] == expected


def test_recovered_tracker_returns_monitoring_to_stable_without_closing_alert(
    api_app,
    db_session,
):
    scope = setup_scope(db_session)
    patient = scope["assigned"]

    critical_event = add_event(
        db_session,
        patient,
        MetricType.HEART_RATE,
        160,
        NOW,
    )
    alert = add_alert(
        db_session,
        patient,
        EvaluationSeverity.CRITICAL,
    )
    tracker = ConditionTracker(
        patient_id=patient.patient_id,
        last_event_id=critical_event.event_id,
        condition_key=ConditionKey.HR_HIGH,
        active=True,
        started_at=alert.detected_at,
        last_seen_at=critical_event.recorded_at,
        confirmed_at=alert.confirmed_at,
    )
    db_session.add(tracker)
    db_session.commit()

    critical_summary = get_list(
        api_app,
        scope["caregiver"],
        scope["household"],
    ).json()["items"][0]["current_summary"]
    assert critical_summary["monitoring_status"] == "CRITICAL"
    assert critical_summary["active_alert_count"] == 1

    recovered_event = add_event(
        db_session,
        patient,
        MetricType.HEART_RATE,
        78,
        NOW + timedelta(minutes=2),
    )
    tracker.last_event_id = recovered_event.event_id
    tracker.last_seen_at = recovered_event.recorded_at
    tracker.active = False
    db_session.commit()

    recovered_summary = get_list(
        api_app,
        scope["caregiver"],
        scope["household"],
    ).json()["items"][0]["current_summary"]

    # The alert remains part of the caregiver workflow, but current patient
    # physiology is no longer reported as Critical after confirmed recovery.
    assert recovered_summary["active_alert_count"] == 1
    assert recovered_summary["highest_active_alert_severity"] == "CRITICAL"
    assert recovered_summary["monitoring_status"] == "STABLE"
    assert recovered_summary["latest_heart_rate"]["value"] == "78.00"


def test_list_query_count_is_constant(api_app, db_session, integration_engine):
    scope = setup_scope(db_session)
    for index in range(8):
        _, patient = add_patient(db_session, scope["household"], f"Patient {index}")
        assign(db_session, scope["caregiver"], patient, scope["admin"])
    db_session.commit()

    statements = []

    def count_statement(*_args):
        statements.append(1)

    event.listen(integration_engine, "before_cursor_execute", count_statement)
    try:
        response = get_list(api_app, scope["caregiver"], scope["household"])
    finally:
        event.remove(integration_engine, "before_cursor_execute", count_statement)
    assert response.status_code == 200
    assert response.json()["total"] == 9
    # Patient-list reads use a fixed number of page/summary queries. Steps,
    # sleep, current monitoring state, and alert workflow each add one bounded
    # query, but the count must remain constant as the patient count grows.
    assert len(statements) <= 8
