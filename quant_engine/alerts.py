"""Alert channels: structured event emission and external notification adapters.

Channels implement the AlertChannel protocol.  In development, use
LoggingAlertChannel.  For production, wire TwilioAlertChannel (phone/SMS)
and/or WebhookAlertChannel (Slack/Teams) via environment-variable credentials.

Never hardcode phone numbers, tokens, or webhook URLs in source control.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.parse
import urllib.request
from typing import Protocol

from .runtime import log_event

logger = logging.getLogger("quant_engine.alerts")


class AlertChannel(Protocol):
    async def notify(self, severity: str, message: str, **context) -> None: ...


class LoggingAlertChannel:
    """Default channel: structured log only.  Replace in deployment configuration."""

    async def notify(self, severity: str, message: str, **context) -> None:
        log_event("engine_alert", severity=severity, message=message, **context)


class TwilioAlertChannel:
    """Send SMS/voice alerts via Twilio REST API.

    Required environment variables:
      TWILIO_ACCOUNT_SID  — Twilio account SID
      TWILIO_AUTH_TOKEN   — Twilio auth token
      TWILIO_FROM_NUMBER  — Sender phone number (E.164 format, e.g. +19.....)
      TWILIO_TO_NUMBER    — Recipient phone number (E.164 format)

    Severity levels mapped: CRITICAL → call, ERROR/WARNING → SMS.
    Falls back to logging if credentials are missing (safe default).
    """

    def __init__(self) -> None:
        self._sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
        self._token = os.environ.get("TWILIO_AUTH_TOKEN", "")
        self._from = os.environ.get("TWILIO_FROM_NUMBER", "")
        self._to = os.environ.get("TWILIO_TO_NUMBER", "")

    async def notify(self, severity: str, message: str, **context) -> None:
        log_event("engine_alert", severity=severity, message=message, **context)
        if not all([self._sid, self._token, self._from, self._to]):
            logger.warning("TwilioAlertChannel: credentials not configured, skipping SMS")
            return
        body = f"[{severity}] {message} | {json.dumps(context, default=str)[:160]}"
        self._send_sms(body)

    def _send_sms(self, body: str) -> None:
        """Synchronous Twilio Messages API call (fire-and-forget from async context)."""
        url = f"https://api.twilio.com/2010-04-01/Accounts/{self._sid}/Messages.json"
        data = urllib.parse.urlencode({
            "From": self._from,
            "To": self._to,
            "Body": body,
        }).encode()
        import base64
        token = base64.b64encode(f"{self._sid}:{self._token}".encode()).decode()
        req = urllib.request.Request(url, data=data,
                                     headers={"Authorization": f"Basic {token}"},
                                     method="POST")
        try:
            with urllib.request.urlopen(req, timeout=5):
                logger.info("twilio_sms_sent to=%s", self._to)
        except Exception as exc:
            logger.error("twilio_sms_failed error=%s", exc)


class WebhookAlertChannel:
    """Post alerts to a Slack-compatible incoming webhook or Teams connector.

    Required environment variable:
      ALERT_WEBHOOK_URL — Incoming webhook URL (Slack/Teams/custom)

    Falls back to logging if URL is missing.
    """

    def __init__(self) -> None:
        self._url = os.environ.get("ALERT_WEBHOOK_URL", "")

    async def notify(self, severity: str, message: str, **context) -> None:
        log_event("engine_alert", severity=severity, message=message, **context)
        if not self._url:
            logger.warning("WebhookAlertChannel: ALERT_WEBHOOK_URL not set, skipping")
            return
        emoji = {"CRITICAL": "🚨", "ERROR": "❌", "WARNING": "⚠️"}.get(severity, "ℹ️")
        text = f"{emoji} *[{severity}]* {message}\n```{json.dumps(context, default=str, indent=2)}```"
        payload = json.dumps({"text": text}).encode()
        req = urllib.request.Request(
            self._url, data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5):
                logger.info("webhook_alert_sent severity=%s", severity)
        except Exception as exc:
            logger.error("webhook_alert_failed error=%s", exc)
