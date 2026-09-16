import asyncio
from datetime import date, datetime, time, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.config import Settings
from app.db.database import get_db
from app.households.model import Household
from app.main import create_app
from app.reminders.enums import (
    ReminderCategory,
    ReminderOccurrenceStatus,
    ReminderPriority,
)
from app.reminders.model import ReminderOccurrence, ReminderTemplate
from app.users.model import User


pytestmark = pytest.mark.integration
NOW = datetime(2026, 9, 16, 12, tzinfo=timezone.utc)
SECRET = "test-reminder-lifecycle-maintenance-secret"


def make_app(db_session, *, configured_secret=SECRET):
    app = create_app(
        Settings(
            environment="testing",
            database_url=None,
            reminder_lifecycle_secret=configured_secret,
        )
    )

    async def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    return app


def request(app, **kwargs):
    async def send():
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            return await client.post(
                "/api/v1/internal/reminders/process-lifecycle",
                **kwargs,
            )

    return asyncio.run(send())


def add_due_occurrences(db_session, patient, *, count):
    household = db_session.get(Household, patient.household_id)
    owner = db_session.get(User, household.created_by_user_id)
    template = ReminderTemplate(
        patient_id=patient.patient_id,
        created_by_user_id=owner.user_id,
        title="Execution endpoint reminder",
        category=ReminderCategory.MEDICATION,
        priority=ReminderPriority.NORMAL,
        start_date=date(2026, 9, 16),
        start_time=time(8),
        timezone="Asia/Manila",
        missed_after_minutes=30,
    )
    db_session.add(template)
    db_session.flush()
    occurrences = [
        ReminderOccurrence(
            reminder_template_id=template.reminder_template_id,
            scheduled_at=NOW - timedelta(minutes=5 - index),
            due_at=NOW + timedelta(minutes=10 + index),
            status=ReminderOccurrenceStatus.UPCOMING,
        )
        for index in range(count)
    ]
    db_session.add_all(occurrences)
    db_session.commit()
    return occurrences


def test_endpoint_requires_configured_constant_time_secret(db_session):
    configured = make_app(db_session)
    assert request(configured).status_code == 401
    assert request(
        configured,
        headers={"X-Alera-Maintenance-Secret": "wrong"},
    ).status_code == 401

    unavailable = make_app(db_session, configured_secret=None)
    response = request(unavailable)
    assert response.status_code == 503
    assert response.json()["detail"] == (
        "Reminder lifecycle execution is not configured."
    )


def test_endpoint_processes_bounded_batches(
    db_session, patient, monkeypatch
):
    occurrences = add_due_occurrences(db_session, patient, count=2)
    monkeypatch.setattr(
        "app.reminders.execution_router.utc_now",
        lambda: NOW,
    )
    app = make_app(db_session)
    auth = {"X-Alera-Maintenance-Secret": SECRET}

    first = request(
        app,
        headers=auth,
        params={"batch_size": 1, "max_batches": 1},
    )
    assert first.status_code == 200
    assert first.json() == {
        "processed": 1,
        "marked_due": 1,
        "marked_missed": 0,
        "batches": 1,
        "limit_reached": True,
    }

    second = request(
        app,
        headers=auth,
        params={"batch_size": 10, "max_batches": 2},
    )
    assert second.status_code == 200
    assert second.json() == {
        "processed": 1,
        "marked_due": 1,
        "marked_missed": 0,
        "batches": 1,
        "limit_reached": False,
    }

    db_session.expire_all()
    assert all(
        db_session.get(ReminderOccurrence, item.reminder_occurrence_id).status
        is ReminderOccurrenceStatus.DUE
        for item in occurrences
    )

    replay = request(app, headers=auth)
    assert replay.status_code == 200
    assert replay.json()["processed"] == 0
    assert replay.json()["batches"] == 0


@pytest.mark.parametrize(
    "query",
    ["batch_size=0", "batch_size=501", "max_batches=0", "max_batches=21"],
)
def test_endpoint_rejects_unsafe_limits(db_session, query):
    app = make_app(db_session)
    response = request(
        app,
        headers={"X-Alera-Maintenance-Secret": SECRET},
        params=dict(part.split("=", 1) for part in query.split("&")),
    )
    assert response.status_code == 422
