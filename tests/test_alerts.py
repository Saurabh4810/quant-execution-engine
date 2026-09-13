"""Unit tests for alerts channels."""
import asyncio
import unittest

from quant_engine.alerts import (
    LoggingAlertChannel,
    TwilioAlertChannel,
    WebhookAlertChannel,
)


class AlertsTests(unittest.IsolatedAsyncioTestCase):
    async def test_logging_alert_channel(self):
        channel = LoggingAlertChannel()
        # Should not raise
        await channel.notify("INFO", "Test message", code=123)

    async def test_twilio_channel_graceful_missing_credentials(self):
        channel = TwilioAlertChannel()
        # Without env vars, should not crash, just log warning
        await channel.notify("CRITICAL", "Position limit exceeded", symbol="CRUDEOIL")

    async def test_webhook_channel_graceful_missing_url(self):
        channel = WebhookAlertChannel()
        # Without env var, should not crash, just log warning
        await channel.notify("WARNING", "High slippage detected", slippage_bps=15)
