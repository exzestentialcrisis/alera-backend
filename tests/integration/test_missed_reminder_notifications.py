from datetime import date, datetime, time, timedelta, timezone
from unittest.mock import Mock

import httpx
import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.devices.model import CaregiverPushDevice
from app.household_access.model import CaregiverPatientAssignment
from app.households.model import Household
from app.notifications.fcm import FCMSender
from app.reminders.enums import (
    ReminderCategory,
    ReminderNotificationChannel,
    ReminderOccurrenceStatus,
    ReminderPriority,
)
from app.reminders.lifecycle import process_reminder_lifecycle
from app.reminders.model import ReminderOccurrence, ReminderTemplate
from app.reminders import notification_service
from app.users.model import AccountStatus, User, UserRole


pytestmark = pytest.mark.integration
NOW = datetime(2026, 9, 16, 12, tzinfo=timezone.utc)


@pytest.fixture()
def reminder_transport(monkeypatch):
    settings = Settings(
        _env_file=None,
        fcm_enabled=True,
        firebase_project_id="alera-test",
        firebase_service_account_json=(
            '{"project_id":"alera-test","client_email":"synthetic",'
            '"private_key":"synthetic"}'
        ),
    )
    monkeypatch.setattr(notification_service, "get_settings", lambda: settings)
    monkeypatch.setattr(FCMSender, "_access_token", lambda self: "mock-oauth")
    post = Mock(return_value=httpx.Response(200, json={"name": "mock-message"}))
    monkeypatch.setattr("app.notifications.fcm.httpx.post", post)
    return post


def add_caregiver_device(db_session, patient, *, token, assigned=True):
    household = db_session.get(Household, patient.household_id)
    owner = db_session.get(User, household.created_by_user_id)
    caregiver = User(
        full_name="Reminder Caregiver",
        role=UserRole.CAREGIVER,
        account_status=AccountStatus.ACTIVE,
    )
    db_session.add(caregiver)
    db_session.flush()
    if assigned:
        db_session.add(
            CaregiverPatientAssignment(
                caregiver_user_id=caregiver.user_id,
                patient_id=patient.patient_id,
                assigned_by_user_id=owner.user_id,
            )
        )
    db_session.add(
        CaregiverPushDevice(
            user_id=caregiver.user_id,
            fcm_token=token,
            platform="ANDROID",
        )
    )
    return caregiver


def add_missed_candidate(db_session, patient, *, channel):
    household = db_session.get(Household, patient.household_id)
    owner = db_session.get(User, household.created_by_user_id)
    template = ReminderTemplate(
        patient_id=patient.patient_id,
        created_by_user_id=owner.user_id,
        title="Evening medication",
        category=ReminderCategory.MEDICATION,
        priority=ReminderPriority.HIGH,
        start_date=date(2026, 9, 16),
        start_time=time(8),
        timezone="Asia/Manila",
        notification_channels=channel,
        missed_after_minutes=30,
    )
    db_session.add(template)
    db_session.flush()
    occurrence = ReminderOccurrence(
        reminder_template_id=template.reminder_template_id,
        scheduled_at=NOW - timedelta(hours=1),
        due_at=NOW - timedelta(minutes=30),
        status=ReminderOccurrenceStatus.DUE,
    )
    db_session.add(occurrence)
    db_session.commit()
    return template, occurrence


def test_missed_push_notifies_only_assigned_caregivers_after_commit_once(
    db_session, patient, reminder_transport
):
    add_caregiver_device(
        db_session, patient, token="assigned-reminder-device", assigned=True
    )
    add_caregiver_device(
        db_session, patient, token="unassigned-reminder-device", assigned=False
    )
    template, occurrence = add_missed_candidate(
        db_session,
        patient,
        channel=ReminderNotificationChannel.PUSH,
    )

    result = process_reminder_lifecycle(db_session, at=NOW)
    assert result.marked_missed == 1
    assert reminder_transport.call_count == 0
    db_session.commit()

    assert reminder_transport.call_count == 1
    assert reminder_transport.call_args.kwargs["json"]["message"] == {
        "token": "assigned-reminder-device",
        "notification": {
            "title": "Missed reminder",
            "body": "Test Patient missed Evening medication.",
        },
        "data": {
            "type": "REMINDER",
            "occurrence_id": str(occurrence.reminder_occurrence_id),
            "template_id": str(template.reminder_template_id),
            "patient_id": str(patient.patient_id),
        },
    }

    replay = process_reminder_lifecycle(db_session, at=NOW)
    db_session.commit()
    assert replay.processed == 0
    assert reminder_transport.call_count == 1


def test_in_app_missed_reminder_does_not_send_push(
    db_session, patient, reminder_transport
):
    add_caregiver_device(db_session, patient, token="assigned-in-app")
    _template, occurrence = add_missed_candidate(
        db_session,
        patient,
        channel=ReminderNotificationChannel.IN_APP,
    )

    process_reminder_lifecycle(db_session, at=NOW)
    db_session.commit()

    assert db_session.get(
        ReminderOccurrence, occurrence.reminder_occurrence_id
    ).status is ReminderOccurrenceStatus.MISSED
    reminder_transport.assert_not_called()


def test_rollback_discards_notification_intent(
    db_session, patient, reminder_transport
):
    add_caregiver_device(db_session, patient, token="assigned-rollback")
    _template, occurrence = add_missed_candidate(
        db_session,
        patient,
        channel=ReminderNotificationChannel.PUSH,
    )

    process_reminder_lifecycle(db_session, at=NOW)
    db_session.rollback()

    db_session.expire_all()
    assert db_session.get(
        ReminderOccurrence, occurrence.reminder_occurrence_id
    ).status is ReminderOccurrenceStatus.DUE
    reminder_transport.assert_not_called()


def test_delivery_failure_never_rolls_back_missed_transition(
    db_session, patient, reminder_transport
):
    add_caregiver_device(db_session, patient, token="assigned-failure")
    _template, occurrence = add_missed_candidate(
        db_session,
        patient,
        channel=ReminderNotificationChannel.PUSH,
    )
    reminder_transport.side_effect = httpx.ConnectError("synthetic failure")

    process_reminder_lifecycle(db_session, at=NOW)
    db_session.commit()

    db_session.expire_all()
    assert db_session.get(
        ReminderOccurrence, occurrence.reminder_occurrence_id
    ).status is ReminderOccurrenceStatus.MISSED
