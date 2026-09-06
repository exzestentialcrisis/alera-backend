"""Explicit local-only issuance/reset; never invoked by application startup."""
import argparse
import os
from uuid import UUID

from sqlalchemy import select

from app.core.config import get_settings
from app.db.database import get_session_factory
from app.household_access.model import PatientAccessCode
from app.household_access.service import issue_access_code
from app.households.model import Household
from app.patients.model import ElderlyPatient
from app.users.model import User

DEMO_PATIENT_ID = UUID("a076ecdb-ae38-4f84-b490-e714977027ee")


def issue_demo_code(db, *, environment: str, reset: bool):
    if environment != "development":
        raise RuntimeError("Demo access codes require explicit development environment.")
    patient = db.scalar(select(ElderlyPatient).where(
        ElderlyPatient.patient_id == DEMO_PATIENT_ID
    ).with_for_update())
    if patient is None:
        raise RuntimeError("Demo patient must already exist.")
    previous = db.scalar(select(PatientAccessCode.access_code_id).where(
        PatientAccessCode.patient_id == DEMO_PATIENT_ID,
        PatientAccessCode.used_at.is_(None),
        PatientAccessCode.revoked_at.is_(None),
    ).limit(1))
    if previous is not None and not reset:
        raise RuntimeError("A code was previously issued; explicitly use --reset to issue another.")
    household = db.get(Household, patient.household_id)
    owner = db.get(User, household.created_by_user_id)
    code, readable = issue_access_code(db, patient.patient_id, owner, 24)
    return readable


def write_private_code(path: str, code: str) -> None:
    """Write the only plaintext copy with owner-only permissions."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as output:
        output.write(code + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true", help="Revoke unused codes and issue a fresh one-time code; existing sessions remain valid.")
    parser.add_argument("--output", required=True, help="New private file for the code (must not exist).")
    args = parser.parse_args()
    # Require an explicit environment variable rather than the development default.
    if os.environ.get("ENVIRONMENT") != "development":
        parser.error("Set ENVIRONMENT=development explicitly for a local development database.")
    settings = get_settings()
    if settings.sql_echo:
        parser.error("Disable SQL_ECHO before issuing demo secrets.")
    with get_session_factory()() as db:
        readable = issue_demo_code(db, environment=settings.environment, reset=args.reset)
        try:
            write_private_code(args.output, readable)
            db.commit()
        except Exception:
            db.rollback()
            os.unlink(args.output)
            raise
    print("One-time demo code written to the private output file; expires in 24 hours.")


if __name__ == "__main__":
    main()
