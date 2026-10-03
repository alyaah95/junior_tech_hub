import logging
import os
from typing import Set

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("AlertManager")

ADMIN_BOT_TOKEN = os.getenv("ADMIN_BOT_TOKEN")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")


class AlertManager:
    """
    Sends critical-failure notifications to a personal/admin Telegram chat
    via a separate Admin Bot. Implemented as a process-wide singleton so the
    in-memory throttle cache is shared across all extractors in a single run,
    guaranteeing at most one alert per (source, field) pair per run.
    """

    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, bot_token: str = None, chat_id: str = None):
        if self._initialized:
            return
        self.bot_token = bot_token or ADMIN_BOT_TOKEN
        self.chat_id = chat_id or ADMIN_CHAT_ID
        self._sent_alerts: Set[str] = set()
        self._initialized = True

    @staticmethod
    def _build_alert_key(source: str, field: str) -> str:
        return f"{source}::{field}"

    def reset_throttle(self):
        """Optional: call at the start of a fresh run if reusing the same process long-term."""
        self._sent_alerts.clear()

    def send_critical_alert(self, source: str, field: str, details: str = "") -> bool:
        alert_key = self._build_alert_key(source, field)

        if alert_key in self._sent_alerts:
            logger.info(f"[AlertManager] Alert for '{alert_key}' already sent this run. Skipping duplicate.")
            return False

        if not self.bot_token or not self.chat_id or "YOUR_" in self.bot_token or "YOUR_" in str(self.chat_id):
            logger.warning(
                f"[AlertManager] ADMIN_BOT_TOKEN/ADMIN_CHAT_ID not configured. "
                f"Would have alerted on '{alert_key}'. Skipping Telegram send."
            )
            self._sent_alerts.add(alert_key)
            return False

        message = (
            "🚨 *CRITICAL SELECTOR FAILURE* 🚨\n\n"
            f"*Source:* `{source}`\n"
            f"*Field:* `{field}`\n"
            f"*Details:* {details}\n\n"
            f"Scraping for `{source}` has been halted to prevent corrupted data. "
            "Other sources will continue normally."
        )

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": "HTML",
        }

        try:
            response = requests.post(url, json=payload, timeout=10)
            if response.status_code == 200:
                logger.info(f"[AlertManager] Alert sent successfully for '{alert_key}'.")
            else:
                logger.error(
                    f"[AlertManager] Failed to send alert (status {response.status_code}): {response.text}"
                )
        except Exception as e:
            logger.error(f"[AlertManager] Exception while sending Telegram alert for '{alert_key}': {e}")
        finally:
            # Throttle regardless of send success, so a dead admin bot can't spam retries per job.
            self._sent_alerts.add(alert_key)

        return True