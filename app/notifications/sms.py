import re

import httpx


class SemaphoreSMSSender:
    """Small Semaphore API adapter; disabled unless explicitly configured."""

    DEFAULT_URL = "https://api.semaphore.co/api/v4/messages"

    def __init__(self, settings):
        self.enabled = settings.sms_enabled
        self.api_key = (
            settings.semaphore_api_key.get_secret_value()
            if settings.semaphore_api_key is not None
            else None
        )
        self.sender_name = settings.semaphore_sender_name
        self.api_url = settings.semaphore_api_url
        self.timeout_seconds = settings.sms_timeout_seconds

    @property
    def configured(self) -> bool:
        return self.enabled and bool(self.api_key)

    @staticmethod
    def normalize_philippine_number(value: str) -> str:
        digits = re.sub(r"\D", "", value)
        if len(digits) == 11 and digits.startswith("09"):
            return "63" + digits[1:]
        if len(digits) == 12 and digits.startswith("639"):
            return digits
        raise ValueError("SMS recipient must be a Philippine mobile number.")

    def send(self, phone_number: str, message: str) -> None:
        if not self.configured:
            return
        payload = {
            "apikey": self.api_key,
            "number": self.normalize_philippine_number(phone_number),
            "message": message[:480],
        }
        if self.sender_name:
            payload["sendername"] = self.sender_name
        response = httpx.post(
            self.api_url or self.DEFAULT_URL,
            data=payload,
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
