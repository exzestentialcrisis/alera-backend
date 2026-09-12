"""add monitoring devices

Revision ID: 0af09d4cb0fc
Revises: a91f0c3d8e72
Create Date: 2026-09-12 16:19:25.878812

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "0af09d4cb0fc"
down_revision: Union[str, Sequence[str], None] = "a91f0c3d8e72"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


monitoring_device_type = postgresql.ENUM(
    "WATCH",
    "PHONE",
    name="monitoring_device_type",
)

device_connection_status = postgresql.ENUM(
    "CONNECTED",
    "DISCONNECTED",
    "UNKNOWN",
    name="device_connection_status",
)

device_network_type = postgresql.ENUM(
    "WIFI",
    "CELLULAR",
    "NONE",
    "UNKNOWN",
    name="device_network_type",
)


def upgrade() -> None:
    """Upgrade schema."""

    bind = op.get_bind()

    device_network_type.create(
    bind,
    checkfirst=True,
    )

    monitoring_device_type.create(
        bind,
        checkfirst=True,
    )

    device_connection_status.create(
        bind,
        checkfirst=True,
    )

    op.create_table(
        "monitoring_devices",

        sa.Column(
           "connection_status",
           postgresql.ENUM(
            name="device_connection_status",
             create_type=False,
                    ),
          server_default=sa.text(
          "'UNKNOWN'::device_connection_status"
           ),
            nullable=False,
        ),
        sa.Column(
            "network_type",
        postgresql.ENUM(
        name="device_network_type",
        create_type=False,
         ),
                server_default=sa.text(
        "'UNKNOWN'::device_network_type"
        ),
            nullable=False,
        ),
        sa.Column(
            "device_id",
            sa.UUID(),
            nullable=False,
        ),
        sa.Column(
            "patient_id",
            sa.UUID(),
            nullable=False,
        ),
        sa.Column(
            "device_type",
            postgresql.ENUM(
                name="monitoring_device_type",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "device_name",
            sa.String(length=100),
            nullable=True,
        ),
        sa.Column(
            "device_model",
            sa.String(length=100),
            nullable=True,
        ),
        sa.Column(
            "battery_percent",
            sa.SmallInteger(),
            nullable=True,
        ),
        sa.Column(
            "reported_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "status_changed_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.CheckConstraint(
            "battery_percent IS NULL "
            "OR battery_percent BETWEEN 0 AND 100",
            name="ck_monitoring_devices_battery_range",
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["elderly_patients.patient_id"],
            name="fk_monitoring_devices_patient_id_elderly_patients",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "device_id",
        ),
        sa.UniqueConstraint(
            "patient_id",
            "device_type",
            name="uq_monitoring_devices_patient_type",
        ),
    )

    op.create_index(
        "ix_monitoring_devices_patient_id",
        "monitoring_devices",
        ["patient_id"],
    )


def downgrade() -> None:
    """Downgrade schema."""

    op.drop_index(
        "ix_monitoring_devices_patient_id",
        table_name="monitoring_devices",
    )

    op.drop_table(
        "monitoring_devices",
    )

    bind = op.get_bind()

    device_network_type.drop(
    bind,
    checkfirst=True,
    )

    device_connection_status.drop(
        bind,
        checkfirst=True,
    )

    monitoring_device_type.drop(
        bind,
        checkfirst=True,
    )