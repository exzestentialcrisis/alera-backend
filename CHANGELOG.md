# Changelog

## Unreleased

- Redesign patient access codes as globally usable 12-character credentials with
  selector-backed lookup, normalized input, and generic authentication failures.
- Require bearer authentication on the caregiver alert API and scope alert reads
  and mutations to active caregiver assignments or care-admin-owned households.
- Add household-scoped caregiver and care-admin login with signed bearer tokens.
- Add reusable bearer actor resolution and an idempotent demo caregiver seed command.
- Add immutable, server-generated household codes with migration backfill.
- Add historical caregiver-to-patient assignments with household-scoped administration.
- Add securely hashed, one-time patient access codes with issuance, replacement, and revocation APIs.
