import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

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
        }.issubset(inspector.get_table_names())

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

        expected_indexes = {
            "alerts": {
                "ix_alerts_patient_status",
                "ix_alerts_patient_condition_detected",
                "ix_alerts_detected_at",
                "uq_alerts_unresolved_patient_condition",
            },
            "alert_actions": {"ix_alert_actions_alert_performed_at"},
            "event_evaluations": {"ix_event_evaluations_alert_id"},
        }
        for table_name, names in expected_indexes.items():
            assert names.issubset(
                {item["name"] for item in inspector.get_indexes(table_name)}
            )

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
            partial_index = connection.execute(
                text(
                    "SELECT indexdef FROM pg_indexes "
                    "WHERE tablename = 'alerts' "
                    "AND indexname = "
                    "'uq_alerts_unresolved_patient_condition'"
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
        assert "ACTIVE" in partial_index
        assert "ACKNOWLEDGED" in partial_index

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
