import logging
import os
import time
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv

from pipelines.postgres_pipeline import PostgresPipeline
from services.message_formatter import format_public_telegram_message

load_dotenv()

logger = logging.getLogger("TelegramPublisher")

PUBLIC_BOT_TOKEN = os.getenv("PUBLIC_BOT_TOKEN", "YOUR_PUBLIC_BOT_TOKEN_HERE")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID", "YOUR_TELEGRAM_CHANNEL_ID_HERE")

TELEGRAM_API_BASE = "https://api.telegram.org"

# Delay between successive posts. Telegram's group/channel limit is roughly
# 20 messages/minute; 2s keeps us comfortably under that with margin for
# retries.
POST_DELAY_SECONDS = 2.0

MAX_RETRIES_ON_429 = 3


class TelegramPublisher:
    """
    Publishes verified job listings to the Public Audience Telegram
    channel/group. Handles formatting (via message_formatter), rate-limit
    backoff, request-level error handling, and DB-backed deduplication so
    the same job is never posted twice.
    """

    def __init__(
        self,
        bot_token: Optional[str] = None,
        channel_id: Optional[str] = None,
        pipeline: Optional[PostgresPipeline] = None,
    ):
        self.bot_token = bot_token or PUBLIC_BOT_TOKEN
        self.channel_id = channel_id or TELEGRAM_CHANNEL_ID
        self.pipeline = pipeline or PostgresPipeline()
        self._configured = bool(
            self.bot_token and self.channel_id
            and "YOUR_" not in self.bot_token
            and "YOUR_" not in str(self.channel_id)
        )

        if not self._configured:
            logger.warning(
                "[TelegramPublisher] PUBLIC_BOT_TOKEN/TELEGRAM_CHANNEL_ID not configured. "
                "Publishing calls will be skipped (logged only)."
            )

    def _send_message(self, text: str) -> bool:
        """
        Sends a single message to the configured channel/group.
        Returns True on success, False on failure (after retries for 429).
        """
        url = f"{TELEGRAM_API_BASE}/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.channel_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        }

        for attempt in range(1, MAX_RETRIES_ON_429 + 1):
            try:
                response = requests.post(url, json=payload, timeout=15)

                if response.status_code == 200:
                    return True

                if response.status_code == 429:
                    retry_after = 5
                    try:
                        retry_after = int(
                            response.json().get("parameters", {}).get("retry_after", 5)
                        )
                    except Exception:
                        pass
                    logger.warning(
                        f"[TelegramPublisher] Rate limited (429). Waiting {retry_after}s "
                        f"before retry (attempt {attempt}/{MAX_RETRIES_ON_429})."
                    )
                    time.sleep(retry_after + 1)
                    continue

                logger.error(
                    f"[TelegramPublisher] Telegram API error {response.status_code}: {response.text}"
                )
                return False

            except requests.exceptions.RequestException as e:
                logger.error(f"[TelegramPublisher] Network/request error while posting: {e}")
                time.sleep(3)

        logger.error("[TelegramPublisher] Exhausted retries after repeated 429s. Dropping this message.")
        return False

    def publish_job(self, job: Dict[str, Any]) -> bool:
        """
        Formats and sends a single job. Does NOT perform dedup checks itself —
        use publish_verified_jobs() for batch publishing with dedup.
        """
        if not self._configured:
            logger.info(f"[TelegramPublisher] (skipped, unconfigured) Would post job: {job.get('title')}")
            return False

        try:
            message_text = format_public_telegram_message(job)
        except Exception as e:
            logger.error(f"[TelegramPublisher] Failed to format job '{job.get('title')}' for publishing: {e}")
            return False

        success = self._send_message(message_text)
        if success:
            logger.info(f"[TelegramPublisher] Published: '{job.get('title')}' @ {job.get('company')}")
        return success

    def publish_verified_jobs(self, source: str, verified_jobs: List[Dict[str, Any]]) -> None:
        """
        Entry point for the orchestrator. Filters out jobs already published
        (via DB cache), posts each remaining job with rate-limit-safe delays,
        and marks successfully posted jobs as published so they're never
        posted again on a future run.
        """
        if not verified_jobs:
            logger.info(f"[{source}] No verified jobs to publish.")
            return

        unpublished_jobs = self.pipeline.filter_unpublished_jobs(source=source, jobs=verified_jobs)
        if not unpublished_jobs:
            logger.info(f"[{source}] All verified jobs were already published. Nothing to do.")
            return

        logger.info(f"[{source}] Publishing {len(unpublished_jobs)} new job(s) to the public channel.")

        published_job_ids = []
        for index, job in enumerate(unpublished_jobs, start=1):
            success = self.publish_job(job)
            if success:
                published_job_ids.append(str(job.get("job_id")))

            # Space out posts even after a failure, so a burst of errors
            # doesn't itself trigger further rate-limit hits.
            if index < len(unpublished_jobs):
                time.sleep(POST_DELAY_SECONDS)

        if published_job_ids:
            self.pipeline.mark_as_published(source=source, job_ids=published_job_ids)

        logger.info(
            f"[{source}] Publishing finished. "
            f"Attempted: {len(unpublished_jobs)} | Successfully published: {len(published_job_ids)}"
        )