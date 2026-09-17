from datetime import date, datetime, time, timedelta, timezone
from unittest.mock import Mock

import httpx
import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.devices.model import PatientPushDevice
from app.notifications.fcm import FCMSender
from app.reminders import notification_service
from app.reminders.enums import (
    ReminderActionType,
    ReminderCategory,
    ReminderNotificationChannel,
    ReminderOccurrenceStatus,
    ReminderPriority,
)
from app.reminders.lifecycle import process_reminder_lifecycle
from app.reminders.model import ReminderAction, ReminderOccurrence, ReminderTemplate
from app.users.model import User


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


def add_due_candidate(db, patient, *, channel, instructions="Take one tablet"):
    from app.households.model import Household

    household = db.get(Household, patient.household_id)
    template = ReminderTemplate(
        patient_id=patient.patient_id,
        created_by_user_id=household.created_by_user_id,
        title="Evening medication",
        category=ReminderCategory.MEDICATION,
        instructions=instructions,
        priority=ReminderPriority.HIGH,
        start_date=date(2026, 9, 16),
        start_time=time(20),
        timezone="Asia/Manila",
        notification_channels=channel,
        missed_after_minutes=30,
    )
    db.add(template)
    db.flush()
    occurrence = ReminderOccurrence(
        reminder_template_id=template.reminder_template_id,
        scheduled_at=NOW - timedelta(seconds=1),
        due_at=NOW + timedelta(minutes=15),
        status=ReminderOccurrenceStatus.UPCOMING,
    )
    db.add(occurrence)
    db.commit()
    return template, occurrence


def test_due_push_notifies_patient_after_commit_once(
    db_session, patient, reminder_transport
):
    db_session.add(
        PatientPushDevice(
            user_id=patient.user_id,
            fcm_token="patient-due-device",
            platform="ANDROID",
        )
    )
    template, occurrence = add_due_candidate(
        db_session, patient, channel=ReminderNotificationChannel.PUSH
    )

    result = process_reminder_lifecycle(db_session, at=NOW)
    assert result.marked_due == 1
    reminder_transport.assert_not_called()
    db_session.commit()

    assert reminder_transport.call_count == 1
    assert reminder_transport.call_args.kwargs["json"]["message"] == {
        "token": "patient-due-device",
        "notification": {
            "title": "Evening medication",
            "body": "Take one tablet",
        },
        "data": {
            "type": "REMINDER_DUE",
            "occurrence_id": str(occurrence.reminder_occurrence_id),
            "template_id": str(template.reminder_template_id),
            "patient_id": str(patient.patient_id),
            "instructions": "Take one tablet",
        },
    }
    action = db_session.scalar(
        select(ReminderAction).where(
            ReminderAction.reminder_occurrence_id
            == occurrence.reminder_occurrence_id
        )
    )
    assert action.action_type is ReminderActionType.MARK_DUE
    assert action.performed_by_user_id is None

    replay = process_reminder_lifecycle(db_session, at=NOW)
    db_session.commit()
    assert replay.processed == 0
    assert reminder_transport.call_count == 1


def test_in_app_due_reminder_does_not_send_push(
    db_session, patient, reminder_transport
):
    db_session.add(
        PatientPushDevice(
            user_id=patient.user_id,
            fcm_token="patient-in-app-device",
            platform="ANDROID",
        )
    )
    _template, occurrence = add_due_candidate(
        db_session, patient, channel=ReminderNotificationChannel.IN_APP
    )

    process_reminder_lifecycle(db_session, at=NOW)
    db_session.commit()
    assert occurrence.status is ReminderOccurrenceStatus.DUE
    reminder_transport.assert_not_called()


def test_rollback_discards_due_notification_intent(
    db_session, patient, reminder_transport
):
    db_session.add(
        PatientPushDevice(
            user_id=patient.user_id,
            fcm_token="patient-rollback-device",
            platform="ANDROID",
        )
    )
    _template, occurrence = add_due_candidate(
        db_session, patient, channel=ReminderNotificationChannel.PUSH
    )

    process_reminder_lifecycle(db_session, at=NOW)
    db_session.rollback()
    db_session.expire_all()
    assert db_session.get(
        ReminderOccurrence, occurrence.reminder_occurrence_id
    ).status is ReminderOccurrenceStatus.UPCOMING
    reminder_transport.assert_not_called()


def test_invalid_patient_registration_is_removed(
    db_session, patient, reminder_transport
):
    device = PatientPushDevice(
        user_id=patient.user_id,
        fcm_token="patient-invalid-device",
        platform="ANDROID",
    )
    db_session.add(device)
    add_due_candidate(db_session, patient, channel=ReminderNotificationChannel.PUSH)
    reminder_transport.return_value = httpx.Response(
        404,
        json={
            "error": {
                "details": [
                    {
                        "@type": "type.googleapis.com/google.firebase.fcm.v1.FcmError",
                        "errorCode": "UNREGISTERED",
                    }
                ]
            }
        },
    )

    process_reminder_lifecycle(db_session, at=NOW)
    db_session.commit()
    db_session.expire_all()
    assert db_session.get(PatientPushDevice, device.id) is None
