import html
import logging
from typing import Any, Dict

logger = logging.getLogger("MessageFormatter")

# Only non-critical fields can legitimately be "N/A" — critical fields
# never reach here as "N/A" because a critical failure raises instead.
NON_CRITICAL_FIELDS = ("company", "location", "job_type")

FIELD_LABELS = {
    "company": "🏢 Company",
    "location": "📍 Location",
    "job_type": "🏠 Type",
}


def format_public_telegram_message(job: Dict[str, Any]) -> str:
    """
    Builds the HTML-formatted public Telegram message for a single verified
    job. Any non-critical field whose value is "N/A" is omitted entirely so
    "N/A" is never shown to the audience. All scraped text is HTML-escaped
    so stray < > & characters in titles/descriptions never break Telegram's
    HTML parse_mode.
    """
    title = html.escape(str(job.get("title", "Untitled Position")))
    lines = [f"💼 <b>{title}</b>"]

    for field in NON_CRITICAL_FIELDS:
        value = job.get(field)
        if value and value != "N/A":
            lines.append(f"{FIELD_LABELS[field]}: {html.escape(str(value))}")

    category = job.get("category")
    if category:
        lines.append(f"🏷 Category: {html.escape(str(category))}")

    description = job.get("description")
    if description:
        lines.append(f"\n📝 {html.escape(str(description))}")

    job_link = job.get("job_link")
    if job_link:
        safe_link = html.escape(str(job_link), quote=True)
        lines.append(f'\n🔗 <a href="{safe_link}">Apply Here</a>')

    return "\n".join(lines)


# Backward-compatible alias — no other module referenced the old name yet,
# kept in case anything does later.
format_job_message_for_public = format_public_telegram_message