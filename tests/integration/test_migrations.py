import os
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


def migrate(database_url, operation, revision):
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


def insert_reminder_action_fixture(connection):
    admin_id, patient_user_id, household_id, patient_id = (uuid4() for _ in range(4))
    connection.execute(
        text(
            "INSERT INTO users (user_id, full_name, role, account_status, created_at, updated_at) "
            "VALUES (:admin_id, 'Reminder Admin', 'CARE_ADMIN', 'ACTIVE', now(), now()), "
            "(:patient_user_id, 'Reminder Patient', 'ELDERLY_PATIENT', 'ACTIVE', now(), now())"
        ),
        {"admin_id": admin_id, "patient_user_id": patient_user_id},
    )
    connection.execute(
        text(
            "INSERT INTO households (household_id, created_by_user_id, household_name, "
            "household_code, household_status, created_at, updated_at) "
            "VALUES (:household_id, :admin_id, 'Reminder Home', 'RMD1-ACTN', "
            "'ACTIVE', now(), now())"
        ),
        {"household_id": household_id, "admin_id": admin_id},
    )
    connection.execute(
        text(
            "INSERT INTO elderly_patients (patient_id, user_id, household_id, normal_hr_min, "
            "normal_hr_max, usual_spo2_min, health_platform, integration_status, created_at, "
            "updated_at) VALUES (:patient_id, :patient_user_id, :household_id, 60, 100, 95, "
            "'SIMULATOR', 'NOT_CONNECTED', now(), now())"
        ),
        {
            "patient_id": patient_id,
            "patient_user_id": patient_user_id,
            "household_id": household_id,
        },
    )
    template_id = connection.execute(
        text(
            "INSERT INTO reminder_templates (patient_id, created_by_user_id, title, category, "
            "start_date, start_time, timezone) VALUES "
            "(:patient_id, :admin_id, 'Reminder', 'OTHER', "
            "CURRENT_DATE, CURRENT_TIME, 'Asia/Manila') "
            "RETURNING reminder_template_id"
        ),
        {"patient_id": patient_id, "admin_id": admin_id},
    ).scalar_one()
    occurrence_id = connection.execute(
        text(
            "INSERT INTO reminder_occurrences (reminder_template_id, scheduled_at, due_at) "
            "VALUES (:template_id, now(), now()) RETURNING reminder_occurrence_id"
        ),
        {"template_id": template_id},
    ).scalar_one()
    return occurrence_id, admin_id


def test_fresh_upgrade_downgrade_and_reupgrade(test_database_url):
    base_url = make_url(test_database_url)
    database_name = f"alera_migration_test_{uuid4().hex[:10]}"
    temporary_url = base_url.set(database=database_name)
    admin_url = base_url.set(database="postgres")
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as connection:
        connection.exec_driver_sql(f'CREATE DATABASE "{database_name}"')

    engine = create_engine(temporary_url)
    try:
        migrate(
            temporary_url.render_as_string(hide_password=False),
            "upgrade",
            "d7b2a1f04c6e",
        )
        with engine.begin() as connection:
            admin_ids = [uuid4(), uuid4()]
            for index, admin_id in enumerate(admin_ids):
                connection.execute(
                    text(
                        "INSERT INTO users (user_id, full_name, role, account_status, "
                        "created_at, updated_at) VALUES (:id, :name, 'CARE_ADMIN', "
                        "'ACTIVE', now(), now())"
                    ),
                    {"id": admin_id, "name": f"Migration Admin {index}"},
                )
                connection.execute(
                    text(
                        "INSERT INTO households (household_id, created_by_user_id, "
                        "household_name, household_status, created_at, updated_at) "
                        "VALUES (:id, :admin_id, :name, 'ACTIVE', now(), now())"
                    ),
                    {
                        "id": uuid4(),
                        "admin_id": admin_id,
                        "name": f"Existing Household {index}",
                    },
                )
        migrate(temporary_url.render_as_string(hide_password=False), "upgrade", "head")
        inspector = inspect(engine)
        assert {
            "users",
            "households",
            "elderly_patients",
            "health_events",
            "event_evaluations",
            "condition_trackers",
            "alerts",
            "alert_actions",
            "caregiver_patient_assignments",
            "patient_access_codes",
            "caregiver_push_devices",
            "reminder_templates",
            "reminder_occurrences",
            "reminder_actions",
        }.issubset(inspector.get_table_names())

        reminder_columns = {
            table_name: {
                column["name"]: column
                for column in inspector.get_columns(table_name)
            }
            for table_name in (
                "reminder_templates",
                "reminder_occurrences",
                "reminder_actions",
            )
        }
        assert {
            "reminder_template_id", "patient_id", "created_by_user_id", "title",
            "category", "instructions", "priority", "start_date", "start_time",
            "timezone", "schedule_rule", "due_after_minutes", "snooze_allowed", "default_snooze_minutes",
            "missed_after_minutes", "notification_channels", "status", "created_at",
            "updated_at", "archived_at",
        } == set(reminder_columns["reminder_templates"])
        assert {
            "reminder_occurrence_id", "reminder_template_id", "scheduled_at",
            "due_at", "status", "created_at", "updated_at",
        } == set(reminder_columns["reminder_occurrences"])
        assert {
            "reminder_action_id", "reminder_occurrence_id", "performed_by_user_id",
            "action_type", "action_note", "previous_status", "new_status",
            "new_due_at", "metadata", "performed_at", "client_action_id",
        } == set(reminder_columns["reminder_actions"])
        for table_name, required_columns in {
            "reminder_templates": {
                "reminder_template_id", "patient_id", "created_by_user_id", "title",
                "category", "priority", "start_date", "start_time", "snooze_allowed",
                "timezone", "due_after_minutes", "default_snooze_minutes", "missed_after_minutes",
                "notification_channels", "status", "created_at", "updated_at",
            },
            "reminder_occurrences": {
                "reminder_occurrence_id", "reminder_template_id", "scheduled_at",
                "due_at", "status", "created_at", "updated_at",
            },
            "reminder_actions": {
                "reminder_action_id", "reminder_occurrence_id", "action_type",
                "performed_at",
            },
        }.items():
            assert all(
                reminder_columns[table_name][column]["nullable"] is False
                for column in required_columns
            )
        assert all(
            reminder_columns["reminder_templates"][column]["nullable"] is True
            for column in ("instructions", "schedule_rule", "archived_at")
        )
        assert all(
            reminder_columns["reminder_actions"][column]["nullable"] is True
            for column in (
                "performed_by_user_id", "action_note", "previous_status",
                "new_status", "new_due_at", "metadata", "client_action_id",
            )
        )
        assert reminder_columns["reminder_actions"]["client_action_id"][
            "default"
        ] is None
        assert reminder_columns["reminder_actions"]["client_action_id"][
            "type"
        ].as_uuid is True
        assert str(reminder_columns["reminder_templates"]["priority"]["default"]) == "'NORMAL'::reminder_priority_enum"
        assert str(reminder_columns["reminder_templates"]["notification_channels"]["default"]) == "'IN_APP'::reminder_notification_channel_enum"
        assert str(reminder_columns["reminder_templates"]["status"]["default"]) == "'ACTIVE'::reminder_template_status_enum"
        assert str(reminder_columns["reminder_occurrences"]["status"]["default"]) == "'UPCOMING'::reminder_occurrence_status_enum"
        assert all(
            "gen_random_uuid()" in str(reminder_columns[table_name][column]["default"])
            for table_name, column in (
                ("reminder_templates", "reminder_template_id"),
                ("reminder_occurrences", "reminder_occurrence_id"),
                ("reminder_actions", "reminder_action_id"),
            )
        )

        with engine.connect() as connection:
            household_codes = connection.execute(
                text("SELECT household_code FROM households")
            ).scalars().all()
        assert len(household_codes) == 2
        assert len(set(household_codes)) == 2
        assert all(
            code and len(code) == 9 and code[4] == "-" for code in household_codes
        )

        def foreign_key_exists(table, columns, referred_table, referred_columns):
            return any(
                item["constrained_columns"] == columns
                and item["referred_table"] == referred_table
                and item["referred_columns"] == referred_columns
                for item in inspector.get_foreign_keys(table)
            )

        assert foreign_key_exists(
            "alerts", ["patient_id"], "elderly_patients", ["patient_id"]
        )
        assert foreign_key_exists(
            "alert_actions", ["alert_id"], "alerts", ["alert_id"]
        )
        assert foreign_key_exists(
            "alert_actions",
            ["performed_by_user_id"],
            "users",
            ["user_id"],
        )
        assert foreign_key_exists(
            "event_evaluations", ["alert_id"], "alerts", ["alert_id"]
        )
        assert foreign_key_exists(
            "reminder_templates", ["patient_id"], "elderly_patients", ["patient_id"]
        )
        assert foreign_key_exists(
            "reminder_templates", ["created_by_user_id"], "users", ["user_id"]
        )
        assert foreign_key_exists(
            "reminder_occurrences", ["reminder_template_id"], "reminder_templates",
            ["reminder_template_id"],
        )
        assert foreign_key_exists(
            "reminder_actions", ["reminder_occurrence_id"], "reminder_occurrences",
            ["reminder_occurrence_id"],
        )
        assert foreign_key_exists(
            "reminder_actions", ["performed_by_user_id"], "users", ["user_id"]
        )

        assert foreign_key_exists(
            "caregiver_push_devices", ["user_id"], "users", ["user_id"]
        )
        assert any(
            item["column_names"] == ["fcm_token"]
            for item in inspector.get_unique_constraints("caregiver_push_devices")
        )

        expected_indexes = {
            "health_events": {"ix_health_events_patient_metric_recorded"},
            "alerts": {
                "ix_alerts_patient_status",
                "ix_alerts_patient_condition_detected",
                "ix_alerts_detected_at",
                "uq_alerts_unresolved_patient_condition_occurrence",
            },
            "alert_actions": {"ix_alert_actions_alert_performed_at"},
            "event_evaluations": {"ix_event_evaluations_alert_id"},
            "reminder_templates": {
                "idx_reminder_templates_created_by",
                "idx_reminder_templates_patient_id",
                "idx_reminder_templates_status",
            },
            "reminder_occurrences": {
                "idx_reminder_occurrences_due_at",
                "idx_reminder_occurrences_status",
                "idx_reminder_occurrences_template_id",
                "uq_reminder_occurrences_template_scheduled_at",
            },
            "reminder_actions": {
                "idx_reminder_actions_occurrence_id",
                "idx_reminder_actions_performed_by",
                "uq_reminder_actions_client_action_id",
            },
        }
        for table_name, names in expected_indexes.items():
            assert names.issubset(
                {item["name"] for item in inspector.get_indexes(table_name)}
            )
        client_action_index = next(
            item
            for item in inspector.get_indexes("reminder_actions")
            if item["name"] == "uq_reminder_actions_client_action_id"
        )
        assert client_action_index["unique"] is True
        assert client_action_index["column_names"] == ["client_action_id"]

        alert_checks = {
            constraint["name"]
            for constraint in inspector.get_check_constraints("alerts")
        }
        assert {
            "ck_alerts_confirmed_at_after_detected_at",
            "ck_alerts_resolved_at_after_confirmed_at",
        }.issubset(alert_checks)

        assert {item["column_names"][0] for item in inspector.get_unique_constraints(
            "health_events"
        )} == {"external_event_id"}
        tracker_uniques = inspector.get_unique_constraints("condition_trackers")
        assert any(
            item["name"] == "uq_condition_trackers_patient_condition"
            and item["column_names"] == ["patient_id", "condition_key"]
            for item in tracker_uniques
        )
        tracker_columns = {
            column["name"]: column
            for column in inspector.get_columns("condition_trackers")
        }
        assert tracker_columns["consecutive_event_count"]["nullable"] is False
        assert str(tracker_columns["consecutive_event_count"]["default"]) == "0"

        patient_columns = {
            column["name"]: column for column in inspector.get_columns("elderly_patients")
        }
        assert patient_columns["normal_hr_min"]["nullable"] is False
        assert patient_columns["normal_hr_max"]["nullable"] is False
        assert patient_columns["usual_spo2_min"]["nullable"] is False
        assert str(patient_columns["normal_hr_min"]["default"]) == "60"
        assert str(patient_columns["normal_hr_max"]["default"]) == "100"
        assert str(patient_columns["usual_spo2_min"]["default"]) == "95"

        checks = {
            constraint["name"]
            for constraint in inspector.get_check_constraints("elderly_patients")
        }
        assert {
            "ck_patient_hr_min_positive",
            "ck_patient_hr_max_positive",
            "ck_patient_hr_range_order",
            "ck_patient_spo2_min_range",
            "ck_patient_spo2_max_range",
            "ck_patient_spo2_range_order",
        }.issubset(checks)
        reminder_checks = {
            constraint["name"]
            for constraint in inspector.get_check_constraints("reminder_templates")
        }
        assert {
            "reminder_default_snooze_nonnegative",
            "reminder_missed_after_nonnegative",
            "reminder_due_after_nonnegative",
        }.issubset(reminder_checks)

        with engine.connect() as connection:
            metric_values = set(
                connection.execute(
                    text(
                        "SELECT enumlabel FROM pg_enum "
                        "JOIN pg_type ON pg_type.oid = pg_enum.enumtypid "
                        "WHERE pg_type.typname = 'metric_type'"
                    )
                ).scalars()
            )
            alert_status_values = connection.execute(
                text(
                    "SELECT enumlabel FROM pg_enum "
                    "JOIN pg_type ON pg_type.oid = pg_enum.enumtypid "
                    "WHERE pg_type.typname = 'alert_status' "
                    "ORDER BY pg_enum.enumsortorder"
                )
            ).scalars().all()
            alert_action_values = connection.execute(
                text(
                    "SELECT enumlabel FROM pg_enum "
                    "JOIN pg_type ON pg_type.oid = pg_enum.enumtypid "
                    "WHERE pg_type.typname = 'alert_action_type' "
                    "ORDER BY pg_enum.enumsortorder"
                )
            ).scalars().all()
            reminder_enums = {
                type_name: connection.execute(
                    text(
                        "SELECT enumlabel FROM pg_enum "
                        "JOIN pg_type ON pg_type.oid = pg_enum.enumtypid "
                        "WHERE pg_type.typname = :type_name "
                        "ORDER BY pg_enum.enumsortorder"
                    ),
                    {"type_name": type_name},
                ).scalars().all()
                for type_name in (
                    "reminder_category_enum", "reminder_priority_enum",
                    "reminder_template_status_enum", "reminder_occurrence_status_enum",
                    "reminder_action_type_enum", "reminder_notification_channel_enum",
                )
            }
            partial_index = connection.execute(
                text(
                    "SELECT indexdef FROM pg_indexes "
                    "WHERE tablename = 'alerts' "
                    "AND indexname = "
                    "'uq_alerts_unresolved_patient_condition_occurrence'"
                )
            ).scalar_one()
        assert {"HEART_RATE", "SPO2", "ACTIVITY", "SLEEP"}.issubset(metric_values)
        assert alert_status_values == [
            "ACTIVE",
            "ACKNOWLEDGED",
            "RESOLVED",
            "FALSE_ALARM",
            "ARCHIVED",
        ]
        assert alert_action_values == [
            "ACKNOWLEDGE",
            "RESOLVE",
            "ESCALATE",
            "MARK_FALSE_ALARM",
            "ADD_NOTE",
            "LOG_INTERVENTION",
        ]
        assert "UNIQUE" in partial_index
        assert "patient_id" in partial_index
        assert "condition_key" in partial_index
        assert "detected_at" in partial_index
        assert "ACTIVE" in partial_index
        assert "ACKNOWLEDGED" in partial_index

        assert reminder_enums == {
            "reminder_category_enum": ["MEDICATION", "HEALTH_CHECK", "HYDRATION", "MEAL", "MOBILITY", "APPOINTMENT", "CHECK_IN", "DEVICE_TASK", "OTHER"],
            "reminder_priority_enum": ["LOW", "NORMAL", "HIGH"],
            "reminder_template_status_enum": ["ACTIVE", "DISABLED", "ARCHIVED"],
            "reminder_occurrence_status_enum": ["UPCOMING", "DUE", "SNOOZED", "COMPLETED", "MISSED", "CANCELED", "COMPLETED_LATE"],
            "reminder_action_type_enum": ["MARK_COMPLETED", "SNOOZE", "REQUEST_HELP", "CAREGIVER_OVERRIDE", "MARK_MISSED", "MARK_MISSED_HANDLED", "RESCHEDULE", "CANCEL", "ADD_NOTE", "FOLLOW_UP"],
            "reminder_notification_channel_enum": ["IN_APP", "PUSH", "SMS"],
        }

        with engine.begin() as connection:
            occurrence_id, admin_id = insert_reminder_action_fixture(connection)
            action_params = {
                "occurrence_id": occurrence_id,
                "admin_id": admin_id,
                "client_action_id": uuid4(),
            }
            action_sql = text(
                "INSERT INTO reminder_actions (reminder_occurrence_id, performed_by_user_id, "
                "action_type, client_action_id) VALUES (:occurrence_id, :admin_id, "
                "'ADD_NOTE', :client_action_id)"
            )
            connection.execute(action_sql, {**action_params, "client_action_id": None})
            connection.execute(action_sql, {**action_params, "client_action_id": None})
            connection.execute(action_sql, action_params)
            with pytest.raises(IntegrityError):
                with connection.begin_nested():
                    connection.execute(action_sql, action_params)

        migrate(
            temporary_url.render_as_string(hide_password=False),
            "downgrade",
            "d4e8f6a1b2c3",
        )
        idempotency_downgraded = inspect(engine)
        assert "client_action_id" not in {
            column["name"]
            for column in idempotency_downgraded.get_columns("reminder_actions")
        }
        assert "uq_reminder_actions_client_action_id" not in {
            index["name"] for index in idempotency_downgraded.get_indexes("reminder_actions")
        }
        migrate(temporary_url.render_as_string(hide_password=False), "upgrade", "head")
        idempotency_reupgraded = inspect(engine)
        assert "client_action_id" in {
            column["name"]
            for column in idempotency_reupgraded.get_columns("reminder_actions")
        }
        assert "uq_reminder_actions_client_action_id" in {
            index["name"] for index in idempotency_reupgraded.get_indexes("reminder_actions")
        }

        # The remainder of this test deliberately crosses the older profile
        # migration, whose downgrade refuses to discard patient rows. Remove
        # only the disposable fixture created for the idempotency assertions.
        with engine.begin() as connection:
            connection.execute(
                text(
                    "TRUNCATE reminder_actions, reminder_occurrences, "
                    "reminder_templates, elderly_patients CASCADE"
                )
            )

        migrate(temporary_url.render_as_string(hide_password=False), "downgrade", "c8d4e52f6b91")
        downgraded_inspector = inspect(engine)
        assert not {"reminder_templates", "reminder_occurrences", "reminder_actions"}.intersection(
            downgraded_inspector.get_table_names()
        )
        with engine.connect() as connection:
            assert connection.execute(
                text(
                    "SELECT typname FROM pg_type WHERE typname LIKE "
                    "'reminder_%_enum'"
                )
            ).scalars().all() == []
        migrate(temporary_url.render_as_string(hide_password=False), "upgrade", "head")

        migrate(
            temporary_url.render_as_string(hide_password=False),
            "downgrade",
            "0f4ec5017182",
        )
        with engine.connect() as connection:
            enum_values = connection.execute(
                text(
                    "SELECT enumlabel FROM pg_enum "
                    "JOIN pg_type ON pg_type.oid = pg_enum.enumtypid "
                    "WHERE pg_type.typname = 'condition_key'"
                )
            ).scalars().all()
            removed_alert_types = connection.execute(
                text(
                    "SELECT typname FROM pg_type "
                    "WHERE typname IN ('alert_status', 'alert_action_type')"
                )
            ).scalars().all()
        # PostgreSQL enum values are intentionally retained by the existing
        # partially irreversible downgrade.
        assert "HR_NORMAL" in enum_values
        assert "SPO2_NORMAL" in enum_values
        # Unlike the older condition_key extension, the Phase 5A enum types
        # are removable after their dependent tables are dropped.
        assert removed_alert_types == []

        migrate(temporary_url.render_as_string(hide_password=False), "upgrade", "head")
        assert "ck_patient_hr_range_order" in {
            constraint["name"]
            for constraint in inspect(engine).get_check_constraints(
                "elderly_patients"
            )
        }
        assert {"alerts", "alert_actions"}.issubset(
            inspect(engine).get_table_names()
        )
        reupgraded_tracker_columns = {
            column["name"]: column
            for column in inspect(engine).get_columns("condition_trackers")
        }
        assert reupgraded_tracker_columns["consecutive_event_count"][
            "nullable"
        ] is False
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
            connection.exec_driver_sql(f'DROP DATABASE IF EXISTS "{database_name}"')
        admin_engine.dispose()


def test_access_code_selector_migration_revokes_legacy_codes_and_preserves_records(test_database_url):
    base_url = make_url(test_database_url)
    database_name = f"alera_selector_migration_{uuid4().hex[:10]}"
    url = base_url.set(database=database_name)
    admin_url = base_url.set(database="postgres")
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as connection:
        connection.exec_driver_sql(f'CREATE DATABASE "{database_name}"')
    engine = create_engine(url)
    try:
        migrate(url.render_as_string(hide_password=False), "upgrade", "f3a9120bc651")
        with engine.begin() as connection:
            admin_id, patient_user_id, household_id, patient_id = (uuid4() for _ in range(4))
            connection.execute(text("INSERT INTO users (user_id, full_name, role, account_status, created_at, updated_at) VALUES (:id, 'Admin', 'CARE_ADMIN', 'ACTIVE', now(), now()), (:patient, 'Patient', 'ELDERLY_PATIENT', 'ACTIVE', now(), now())"), {"id": admin_id, "patient": patient_user_id})
            connection.execute(text("INSERT INTO households (household_id, created_by_user_id, household_name, household_code, household_status, created_at, updated_at) VALUES (:id, :admin, 'Home', 'ABCD-EFGH', 'ACTIVE', now(), now())"), {"id": household_id, "admin": admin_id})
            connection.execute(text("INSERT INTO elderly_patients (patient_id, user_id, household_id, normal_hr_min, normal_hr_max, usual_spo2_min, health_platform, integration_status, created_at, updated_at) VALUES (:id, :user, :household, 60, 100, 95, 'SIMULATOR', 'NOT_CONNECTED', now(), now())"), {"id": patient_id, "user": patient_user_id, "household": household_id})
            connection.execute(text("INSERT INTO patient_access_codes (access_code_id, patient_id, code_hash, created_by_user_id, created_at, expires_at) VALUES (:id, :patient, 'legacy-hash', :admin, now(), now() + interval '1 day')"), {"id": uuid4(), "patient": patient_id, "admin": admin_id})
        migrate(url.render_as_string(hide_password=False), "upgrade", "head")
        with engine.connect() as connection:
            row = connection.execute(text("SELECT access_code_selector, revoked_at FROM patient_access_codes")).one()
            assert row.access_code_selector is None and row.revoked_at is not None
            assert connection.execute(text("SELECT count(*) FROM elderly_patients")).scalar_one() == 1
            assert "ix_patient_access_codes_active_selector" in {item["name"] for item in inspect(engine).get_indexes("patient_access_codes")}
        migrate(url.render_as_string(hide_password=False), "downgrade", "f3a9120bc651")
        assert "access_code_selector" not in {column["name"] for column in inspect(engine).get_columns("patient_access_codes")}
        migrate(url.render_as_string(hide_password=False), "upgrade", "head")
        assert "access_code_selector" in {column["name"] for column in inspect(engine).get_columns("patient_access_codes")}
    finally:
        engine.dispose()
        with admin_engine.connect() as connection:
            connection.execute(text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :name AND pid <> pg_backend_pid()"), {"name": database_name})
            connection.exec_driver_sql(f'DROP DATABASE IF EXISTS "{database_name}"')
        admin_engine.dispose()
