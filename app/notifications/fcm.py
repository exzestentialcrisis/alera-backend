"""Optional HTTP v1 sender. Never log credentials, tokens, or remote error bodies."""

import json
import logging
import re

import httpx

from app.core.config import Settings

logger = logging.getLogger(__name__)
SCOPE = "https://www.googleapis.com/auth/firebase.messaging"


class FCMSender:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.credentials = None

    @property
    def configured(self) -> bool:
        if not self.settings.fcm_enabled:
            logger.debug("FCM delivery disabled.")
            return False
        if (
            not self.settings.firebase_project_id
            or not self.settings.firebase_service_account_json
        ):
            logger.warning("FCM configuration missing; delivery skipped.")
            return False
        if not re.fullmatch(
            r"[a-z][a-z0-9-]{4,61}[a-z0-9]", self.settings.firebase_project_id
        ):
            logger.warning("FCM configuration invalid; delivery skipped.")
            return False
        return True

    def _access_token(self) -> str:
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account

        if self.credentials is None:
            info = json.loads(
                self.settings.firebase_service_account_json.get_secret_value()
            )
            # Use Google's OAuth endpoint regardless of the JSON's token_uri.
            info["token_uri"] = "https://oauth2.googleapis.com/token"
            self.credentials = service_account.Credentials.from_service_account_info(
                info, scopes=[SCOPE]
            )
        if not self.credentials.valid:
            request = Request()
            try:
                self.credentials.refresh(
                    lambda *args, **kwargs: request(*args, **{**kwargs, "timeout": 10})
                )
            finally:
                request.session.close()
        return self.credentials.token

    def send(self, token: str, *, alert_id, patient_id) -> bool:
        """Return True only for a definitively invalid device registration."""
        if not self.configured:
            return False
        try:
            access_token = self._access_token()
            response = httpx.post(
                f"https://fcm.googleapis.com/v1/projects/{self.settings.firebase_project_id}/messages:send",
                headers={"Authorization": f"Bearer {access_token}"},
                json={
                    "message": {
                        "token": token,
                        "notification": {
                            "title": "Alera health alert",
                            "body": "A new alert needs your attention.",
                        },
                        "data": {
                            "type": "ALERT",
                            "alert_id": str(alert_id),
                            "patient_id": str(patient_id),
                        },
                    }
                },
                timeout=10,
            )
            if response.is_success:
                return False
            details = response.json().get("error", {}).get("details", [])
            invalid = any(
                item.get("@type")
                == "type.googleapis.com/google.firebase.fcm.v1.FcmError"
                and item.get("errorCode") in {"UNREGISTERED", "INVALID_ARGUMENT"}
                for item in details
            )
            logger.warning(
                "FCM registration rejected." if invalid else "FCM delivery failed."
            )
            return invalid
        except Exception:
            logger.warning("FCM delivery unavailable.")
            return False
