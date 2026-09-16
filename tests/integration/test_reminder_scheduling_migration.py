import os
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings


pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]
PREVIOUS_REVISION = "7e8f9012a3b4"
RECONCILIATION_REVISION = "9a4c6e8f1b2d"


def migrate(database_url: str, operation: str, revision: str) -> None:
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = database_url
    get_settings.cache_clear()
    try:
        config = Config(str(ROOT / "alembic.ini"))
        getattr(command, operation)(config, revision)
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous
        get_settings.cache_clear()


@contextmanager
def temporary_database(test_database_url: str, prefix: str):
    base_url = make_url(test_database_url)
    database_name = f"{prefix}_{uuid4().hex[:10]}"
    database_url = base_url.set(database=database_name)
    admin_url = base_url.set(database="postgres")
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as connection:
        connection.exec_driver_sql(f'CREATE DATABASE "{database_name}"')

    engine = create_engine(database_url)
    try:
        yield database_url.render_as_string(hide_password=False), engine
    finally:
        engine.dispose()
        with admin_engine.connect() as connection:
            connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :database_name AND pid <> pg_backend_pid()"
                ),
                {"database_name": database_name},
            )
            connection.exec_driver_sql(
                f'DROP DATABASE IF EXISTS "{database_name}"'
            )
        admin_engine.dispose()


def insert_template_fixture(
    connection,
    *,
    title: str = "Legacy reminder",
    timezone: str | None = None,
):
    admin_id, patient_user_id, household_id, patient_id = (
        uuid4() for _ in range(4)
    )
    connection.execute(
        text(
            "INSERT INTO users "
            "(user_id, full_name, role, account_status, created_at, updated_at) "
            "VALUES (:admin, 'Scheduling Admin', 'CARE_ADMIN', 'ACTIVE', now(), now()), "
            "(:patient_user, 'Scheduling Patient', 'ELDERLY_PATIENT', "
            "'ACTIVE', now(), now())"
        ),
        {"admin": admin_id, "patient_user": patient_user_id},
    )
    connection.execute(
        text(
            "INSERT INTO households "
            "(household_id, created_by_user_id, household_name, household_code, "
            "household_status, created_at, updated_at) "
            "VALUES (:household, :admin, 'Scheduling Home', :code, "
            "'ACTIVE', now(), now())"
        ),
        {
            "household": household_id,
            "admin": admin_id,
            "code": f"{uuid4().hex[:4].upper()}-{uuid4().hex[:4].upper()}",
        },
    )
    connection.execute(
        text(
            "INSERT INTO elderly_patients "
            "(patient_id, user_id, household_id, normal_hr_min, normal_hr_max, "
            "usual_spo2_min, health_platform, integration_status, created_at, "
            "updated_at) VALUES (:patient, :patient_user, :household, 60, 100, "
            "95, 'SIMULATOR', 'NOT_CONNECTED', now(), now())"
        ),
        {
            "patient": patient_id,
            "patient_user": patient_user_id,
            "household": household_id,
        },
    )
    if timezone is None:
        statement = text(
            "INSERT INTO reminder_templates "
            "(patient_id, created_by_user_id, title, category, start_date, "
            "start_time) VALUES (:patient, :admin, :title, 'OTHER', "
            "CURRENT_DATE, CURRENT_TIME) RETURNING reminder_template_id"
        )
    else:
        statement = text(
            "INSERT INTO reminder_templates "
            "(patient_id, created_by_user_id, title, category, start_date, "
            "start_time, timezone) VALUES (:patient, :admin, :title, 'OTHER', "
            "CURRENT_DATE, CURRENT_TIME, :timezone) "
            "RETURNING reminder_template_id"
        )
    return connection.execute(
        statement,
        {
            "patient": patient_id,
            "admin": admin_id,
            "title": title,
            "timezone": timezone,
        },
    ).scalar_one()


def assert_scheduling_contract(engine) -> None:
    inspector = inspect(engine)
    columns = {
        column["name"]: column
        for column in inspector.get_columns("reminder_templates")
    }
    assert columns["timezone"]["nullable"] is False
    assert str(columns["timezone"]["type"]) == "VARCHAR"
    assert columns["timezone"]["default"] is None
    assert columns["due_after_minutes"]["nullable"] is False
    assert str(columns["due_after_minutes"]["type"]) == "SMALLINT"
    assert str(columns["due_after_minutes"]["default"]) == "15"

    checks = {
        item["name"]: item["sqltext"]
        for item in inspector.get_check_constraints("reminder_templates")
    }
    assert "due_after_minutes >= 0" in checks[
        "reminder_due_after_nonnegative"
    ]

    index = next(
        item
        for item in inspector.get_indexes("reminder_occurrences")
        if item["name"]
        == "uq_reminder_occurrences_template_scheduled_at"
    )
    assert index["unique"] is True
    assert index["column_names"] == ["reminder_template_id", "scheduled_at"]


def test_reminder_scheduling_reconciliation_clean_path(test_database_url):
    with temporary_database(
        test_database_url, "alera_reminder_scheduling_clean"
    ) as (database_url, engine):
        migrate(database_url, "upgrade", PREVIOUS_REVISION)
        with engine.begin() as connection:
            legacy_template_id = insert_template_fixture(connection)

        migrate(database_url, "upgrade", RECONCILIATION_REVISION)
        assert_scheduling_contract(engine)

        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT timezone, due_after_minutes FROM reminder_templates "
                    "WHERE reminder_template_id = :template_id"
                ),
                {"template_id": legacy_template_id},
            ).one()
            assert row.timezone == "Asia/Manila"
            assert row.due_after_minutes == 15

        with engine.begin() as connection:
            with pytest.raises(IntegrityError):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "UPDATE reminder_templates "
                            "SET due_after_minutes = -1 "
                            "WHERE reminder_template_id = :template_id"
                        ),
                        {"template_id": legacy_template_id},
                    )

            second_template_id = insert_template_fixture(
                connection,
                title="Second reminder",
                timezone="Asia/Manila",
            )
            scheduled_at = "2026-09-16T08:00:00+00:00"
            connection.execute(
                text(
                    "INSERT INTO reminder_occurrences "
                    "(reminder_template_id, scheduled_at, due_at) "
                    "VALUES (:template_id, :scheduled_at, :scheduled_at)"
                ),
                {
                    "template_id": legacy_template_id,
                    "scheduled_at": scheduled_at,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO reminder_occurrences "
                    "(reminder_template_id, scheduled_at, due_at) "
                    "VALUES (:template_id, :scheduled_at, :scheduled_at)"
                ),
                {
                    "template_id": second_template_id,
                    "scheduled_at": scheduled_at,
                },
            )
            with pytest.raises(IntegrityError):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "INSERT INTO reminder_occurrences "
                            "(reminder_template_id, scheduled_at, due_at) "
                            "VALUES (:template_id, :scheduled_at, :scheduled_at)"
                        ),
                        {
                            "template_id": legacy_template_id,
                            "scheduled_at": scheduled_at,
                        },
                    )

        migrate(database_url, "downgrade", PREVIOUS_REVISION)
        downgraded = inspect(engine)
        assert {"timezone", "due_after_minutes"}.isdisjoint(
            {
                column["name"]
                for column in downgraded.get_columns("reminder_templates")
            }
        )
        assert "uq_reminder_occurrences_template_scheduled_at" not in {
            item["name"]
            for item in downgraded.get_indexes("reminder_occurrences")
        }

        migrate(database_url, "upgrade", RECONCILIATION_REVISION)
        assert_scheduling_contract(engine)


def test_reminder_scheduling_reconciliation_adopts_existing_schema(
    test_database_url,
):
    with temporary_database(
        test_database_url, "alera_reminder_scheduling_adoption"
    ) as (database_url, engine):
        migrate(database_url, "upgrade", PREVIOUS_REVISION)
        with engine.begin() as connection:
            template_id = insert_template_fixture(connection)
            connection.execute(
                text(
                    "ALTER TABLE reminder_templates "
                    "ADD COLUMN timezone VARCHAR DEFAULT 'Asia/Manila'"
                )
            )
            connection.execute(
                text(
                    "UPDATE reminder_templates SET timezone = 'Asia/Manila' "
                    "WHERE timezone IS NULL"
                )
            )
            connection.execute(
                text(
                    "ALTER TABLE reminder_templates "
                    "ALTER COLUMN timezone SET NOT NULL, "
                    "ALTER COLUMN timezone DROP DEFAULT"
                )
            )
            connection.execute(
                text(
                    "ALTER TABLE reminder_templates ADD COLUMN "
                    "due_after_minutes SMALLINT NOT NULL DEFAULT 15"
                )
            )
            connection.execute(
                text(
                    "ALTER TABLE reminder_templates ADD CONSTRAINT "
                    "reminder_due_after_nonnegative "
                    "CHECK (due_after_minutes >= 0)"
                )
            )
            connection.execute(
                text(
                    "CREATE UNIQUE INDEX "
                    "uq_reminder_occurrences_template_scheduled_at "
                    "ON reminder_occurrences "
                    "(reminder_template_id, scheduled_at)"
                )
            )
            object_ids_before = connection.execute(
                text(
                    "SELECT "
                    "to_regclass('uq_reminder_occurrences_template_scheduled_at')::oid, "
                    "(SELECT oid FROM pg_constraint WHERE "
                    "conname = 'reminder_due_after_nonnegative')"
                )
            ).one()

        migrate(database_url, "upgrade", RECONCILIATION_REVISION)
        assert_scheduling_contract(engine)

        with engine.connect() as connection:
            object_ids_after = connection.execute(
                text(
                    "SELECT "
                    "to_regclass('uq_reminder_occurrences_template_scheduled_at')::oid, "
                    "(SELECT oid FROM pg_constraint WHERE "
                    "conname = 'reminder_due_after_nonnegative')"
                )
            ).one()
            row = connection.execute(
                text(
                    "SELECT title, timezone, due_after_minutes "
                    "FROM reminder_templates "
                    "WHERE reminder_template_id = :template_id"
                ),
                {"template_id": template_id},
            ).one()

        assert object_ids_after == object_ids_before
        assert row == ("Legacy reminder", "Asia/Manila", 15)
