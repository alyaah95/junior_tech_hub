import asyncio
import logging
import math
import os
from pathlib import Path
import re
import time
import random
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from dotenv import load_dotenv
from parsel import Selector
from playwright.sync_api import sync_playwright
try:
    from playwright_stealth import stealth_sync
except ImportError:
    stealth_sync = None

from extractors.base_extractor import BaseExtractor
from core.exceptions import CriticalSelectorError
from services.alert_manager import AlertManager

load_dotenv()

logger = logging.getLogger("WuzzufExtractor")


def get_random_delay(min_sec=2.0, max_sec=4.0):
    return random.uniform(min_sec, max_sec)


class WuzzufExtractor(BaseExtractor):

    CARD_SELECTOR = "a[href*='/jobs/p/']"
    CHALLENGE_TITLES = (
        "just a moment", 
        "cloudflare", 
        "attention required", 
        "checking your browser"
    )

    def __init__(self, search_urls: Optional[List[str]] = None):
        super().__init__(source_name="Wuzzuf")

        self.search_urls = search_urls or [
            "https://wuzzuf.net/search/jobs/?q=web+developer&a=hpb",
            "https://wuzzuf.net/search/jobs/?q=data+engineer&a=hpb",
        ]

        self.alert_manager = AlertManager()

        self.EXCLUDED_KEYWORDS = [
            "senior", "sr", "sr.", "lead", "principal", "architect", "manager",
            "head", "director", "staff", "executive", "team lead", "supervisor",
            "chief", "vp", "expert", "consultant", "marketing", "seo", "sem",
            "digital marketing", "media buyer", "content creator", "copywriter",
            "sales", "account manager", "business development", "telesales",
            "crm", "designer", "ui/ux", "ui", "ux", "graphic designer",
            "illustrator", "animator", "video editor", "3d", "creative", "hr",
            "human resources", "recruiter", "talent acquisition", "accountant",
            "finance", "admin", "receptionist", "office manager", "operation",
            "operations", "supply chain", "logistics", "devops", "site reliability",
            "sre", "qa", "quality assurance", "testing", "test engineer",
            "mobile", "android", "ios", "flutter", "react native", "embedded",
            "firmware", "hardware", "cyber security", "security engineer",
            "penetration", "network", "system administrator", "sysadmin",
            "helpdesk", "technical support", "instructor", "teacher", "tutor",
            "data center", "datacenter", "infrastructure"
        ]

    def _build_page_url(self, base_url: str, page_num: int) -> str:
        parsed = urlparse(base_url)
        query_params = parse_qs(parsed.query)
        query_params["start"] = [str(page_num - 1)]
        new_query = urlencode(query_params, doseq=True)
        return urlunparse((
            parsed.scheme, parsed.netloc, parsed.path,
            parsed.params, new_query, parsed.fragment
        ))

    # ---------- Context & Stealth Setup ----------

    def _build_context(self, browser):
        proxy_url = os.getenv("WUZZUF_PROXY")
        kwargs = dict(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1366, "height": 768},
            locale="en-US",
            timezone_id="Africa/Cairo",
        )
        if proxy_url:
            kwargs["proxy"] = {"server": proxy_url}
        return browser.new_context(**kwargs)

    def _apply_stealth(self, page):
        if stealth_sync:
            try:
                stealth_sync(page)
            except Exception as e:
                logger.debug(f"Could not apply stealth_sync: {e}")

    def _route_handler(self, route):
        req = route.request
        if "/cdn-cgi/" in req.url or "challenges.cloudflare.com" in req.url:
            return route.continue_()
        if req.resource_type in ("image", "media", "font"):
            return route.abort()
        return route.continue_()

    def _simulate_human_behavior(self, page):
        try:
            viewport = page.viewport_size or {"width": 1366, "height": 768}
            for _ in range(random.randint(1, 2)):
                x = random.randint(100, viewport["width"] - 100)
                y = random.randint(100, viewport["height"] - 100)
                page.mouse.move(x, y, steps=random.randint(5, 10))

            page.evaluate("window.scrollBy(0, 300);")
        except Exception:
            pass

    # ---------- Challenge & Hydration Handling ----------

    def _is_challenge(self, page) -> bool:
        try:
            title = (page.title() or "").lower()
        except Exception:
            return True
        return any(t in title for t in self.CHALLENGE_TITLES)

    def _save_debug(self, page, tag: str):
        try:
            out = Path("debug_artifacts")
            out.mkdir(exist_ok=True)
            page.screenshot(path=str(out / f"{tag}.png"), full_page=True)
            (out / f"{tag}.html").write_text(page.content(), encoding="utf-8")
        except Exception as e:
            logger.debug(f"Could not save debug artifacts: {e}")

    def _wait_for_job_cards(self, page, timeout_ms: int = 60000) -> bool:
        deadline = time.monotonic() + timeout_ms / 1000
        logged = False
        while time.monotonic() < deadline:
            if self._is_challenge(page):
                if not logged:
                    logger.warning("Cloudflare challenge page present; waiting for it to resolve...")
                    logged = True
                page.wait_for_timeout(1500)
                continue
            try:
                page.wait_for_selector(self.CARD_SELECTOR, state="attached", timeout=3000)
                n1 = page.locator(self.CARD_SELECTOR).count()
                page.wait_for_timeout(800)
                n2 = page.locator(self.CARD_SELECTOR).count()
                if n1 and n1 == n2:
                    return True
            except Exception:
                pass
        return False

    def _load_listing_page(self, page, url: str, tag: str, retries: int = 2) -> str:
        for attempt in range(1, retries + 2):
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
            except Exception as e:
                logger.warning(f"[{tag}] goto failed (attempt {attempt}): {e}")
            self._simulate_human_behavior(page)

            if self._wait_for_job_cards(page):
                return page.content()

            logger.warning(f"[{tag}] No job cards after attempt {attempt}/{retries + 1}.")
            self._save_debug(page, f"{tag}_attempt{attempt}")
            time.sleep(get_random_delay(4.0, 8.0))
        return ""

    # ---------- Filters & Parsing ----------

    def _is_title_allowed(self, title: str) -> bool:
        title_clean = title.lower()
        words = set(re.findall(r"\b\w+\b", title_clean))
        for keyword in self.EXCLUDED_KEYWORDS:
            if " " in keyword:
                if keyword in title_clean:
                    return False
            else:
                if keyword in words:
                    return False
        return True

    def is_recently_posted(self, posted_at_text: str, max_days: int = 30) -> bool:
        if not posted_at_text:
            return True
        text = posted_at_text.lower().strip()
        if "30+ day" in text or "30+ days" in text or "+30 day" in text:
            return False
        if any(k in text for k in ["today", "just now", "moment", "hour", "minute", "yesterday"]):
            return True

        match = re.search(r'(\d+)\+?\s+(day|week|month|year)', text)
        if match:
            num = int(match.group(1))
            unit = match.group(2)
            if "day" in unit: days_ago = num
            elif "week" in unit: days_ago = num * 7
            elif "month" in unit: days_ago = num * 30
            elif "year" in unit: days_ago = num * 365
            else: days_ago = 0
            return days_ago <= max_days
        return True

    def _get_total_pages(self, selector: Selector) -> int:
        try:
            jobs_count_text = selector.xpath(
                "//span[contains(@class, 'css-')]//strong/text() | "
                "//div[contains(@class, 'css-')]//p//strong/text() | "
                "//strong/text()"
            ).getall()
            
            for text in jobs_count_text:
                clean_num = re.sub(r'[^\d]', '', text)
                if clean_num and len(clean_num) <= 5: 
                    total_jobs = int(clean_num)
                    if total_jobs > 0:
                        calculated_pages = math.ceil(total_jobs / 15)
                        return min(calculated_pages, 10)

            page_numbers = selector.xpath(
                "//nav//ul//li//button//text() | "
                "//nav//ul//li//a//text() | "
                "//ul//li//button//text()"
            ).getall()
            
            valid_pages = [int(p.strip()) for p in page_numbers if p.strip().isdigit()]
            if valid_pages:
                max_page = max(valid_pages)
                return min(max_page, 10)

        except Exception as e:
            logger.warning(f"Could not parse total pages for Wuzzuf: {e}")

        return 1

    def _extract_job_id(self, url: str) -> str:
        match = re.search(r"/jobs/p/([a-zA-Z0-9-]+)", url)
        if match:
            return match.group(1)[:150]
        return url[:150]

    def _parse_html_to_raw_jobs(self, html_content: str) -> List[Dict[str, Any]]:
        raw_jobs = []
        selector = Selector(text=html_content)
        
        job_links = selector.xpath("//a[contains(@href, '/jobs/p/')]")

        for link_node in job_links:
            raw_link = link_node.xpath(".//@href").get("")
            title = link_node.xpath(".//text()").get("").strip()

            if not raw_link:
                self.alert_manager.send_critical_alert(
                    source=self.source_name,
                    field="job_link",
                    details="Could not extract 'href' from a job card anchor matching '/jobs/p/'."
                )
                raise CriticalSelectorError(source=self.source_name, field="job_link")

            if not title or len(title) < 3:
                self.alert_manager.send_critical_alert(
                    source=self.source_name,
                    field="title",
                    details=f"Could not extract a valid title for job link '{raw_link}'."
                )
                raise CriticalSelectorError(source=self.source_name, field="title")

            if not self._is_title_allowed(title):
                continue

            job_link = raw_link if raw_link.startswith("http") else f"https://wuzzuf.net{raw_link}"
            job_id = self._extract_job_id(job_link)

            card = link_node.xpath(".//a[contains(@href, '/jobs/p/')]/ancestor::div[contains(@class, 'css-')][2]")
            if not card:
                card = link_node.xpath("./ancestor::div[2]")

            company = card.xpath(".//a[contains(@href, '/jobs/careers/')]/text()").get()
            if not company:
                logger.warning(f"[{self.source_name}] Non-critical field 'company' missing for job '{title}'. Defaulting to 'N/A'.")
                company = "N/A"
            else:
                company = company.strip()

            loc_parts = card.xpath(".//a[contains(@href, '/jobs/careers/')]/following-sibling::span//text() | .//span[contains(text(), 'Egypt') or contains(text(), 'Cairo') or contains(text(), 'Alexandria')]//text()").getall()
            location = ", ".join([p.strip() for p in loc_parts if p.strip() and p.strip() != "-"]) if loc_parts else ""
            if not location:
                logger.warning(f"[{self.source_name}] Non-critical field 'location' missing for job '{title}'. Defaulting to 'N/A'.")
                location = "N/A"

            badges_text = " ".join([b.strip().lower() for b in card.xpath(".//a/span/text()").getall()])
            if "remote" in badges_text or "work from home" in badges_text or "عن بعد" in badges_text:
                job_type = "Remote"
            elif "hybrid" in badges_text or "هجين" in badges_text:
                job_type = "Hybrid"
            elif badges_text:
                job_type = "On-site"
            else:
                logger.warning(f"[{self.source_name}] Non-critical field 'job_type' could not be determined for job '{title}'. Defaulting to 'N/A'.")
                job_type = "N/A"

            posted_at = card.xpath(".//div[contains(text(), 'ago') or contains(text(), 'just now')]/text()").get("").strip()
            if not posted_at:
                self.alert_manager.send_critical_alert(
                    source=self.source_name,
                    field="posted_at",
                    details=f"Could not extract 'posted_at' for job '{title}' ({job_link})."
                )
                raise CriticalSelectorError(source=self.source_name, field="posted_at")

            if not self.is_recently_posted(posted_at):
                continue

            card_texts = card.xpath(".//text()").getall()
            clean_card_text = " ".join([t.strip() for t in card_texts if t.strip()])

            if not clean_card_text:
                self.alert_manager.send_critical_alert(
                    source=self.source_name,
                    field="description",
                    details=f"Could not extract any card text for job '{title}' ({job_link})."
                )
                raise CriticalSelectorError(source=self.source_name, field="description")

            raw_description = clean_card_text if len(clean_card_text) > 30 else title

            raw_jobs.append({
                "job_id": job_id,
                "title": title,
                "company": company,
                "location": location,
                "job_type": job_type,
                "job_link": job_link,
                "raw_description": raw_description,
                "posted_at": posted_at,
                "source": self.source_name
            })
        return raw_jobs

    # ---------- Main Extraction Flow ----------

    def extract(self) -> List[Dict[str, Any]]:
        all_raw_jobs, seen_job_ids = [], set()
        headless = os.getenv("WUZZUF_HEADLESS", "1") != "0"

        logger.info(f"=== Starting Wuzzuf Scraping for {len(self.search_urls)} Search URLs ===")

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=headless,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-infobars",
                    "--window-size=1366,768"
                ],
            )
            try:
                context = self._build_context(browser)
                page = context.new_page()
                page.route("**/*", self._route_handler)
                self._apply_stealth(page)

                for url_index, search_url in enumerate(self.search_urls, start=1):
                    if url_index > 1:
                        time.sleep(get_random_delay(3.0, 5.0))

                    first_html = self._load_listing_page(
                        page, self._build_page_url(search_url, 1), f"url{url_index}_p1"
                    )
                    
                    if not first_html:
                        logger.error(
                            f"URL [{url_index}] blocked or never rendered; skipping. "
                            "Check debug_artifacts/ and runner IP."
                        )
                        continue

                    total_pages = self._get_total_pages(Selector(text=first_html))

                    for page_num in range(1, total_pages + 1):
                        if page_num == 1:
                            html_content = first_html
                        else:
                            time.sleep(get_random_delay(2.5, 4.0))
                            html_content = self._load_listing_page(
                                page, self._build_page_url(search_url, page_num),
                                f"url{url_index}_p{page_num}"
                            )
                            if not html_content:
                                break

                        added = 0
                        for job in self._parse_html_to_raw_jobs(html_content):
                            if job["job_id"] not in seen_job_ids:
                                seen_job_ids.add(job["job_id"])
                                all_raw_jobs.append(job)
                                added += 1

                        logger.info(
                            f"URL [{url_index}/{len(self.search_urls)}] | "
                            f"Page {page_num}/{total_pages}: Extracted {added} new jobs."
                        )
            finally:
                browser.close()

        # Graceful Handling: إرسال تنبيه واستمرار باقي السكريبتات بدلاً من إيقاف السلسلة بالكامل
        if not all_raw_jobs:
            logger.error("Wuzzuf extraction yielded 0 jobs (likely Cloudflare block). Sending alert...")
            self.alert_manager.send_critical_alert(
                source=self.source_name,
                field="cloudflare_block",
                details="Wuzzuf yielded 0 jobs on GitHub Actions. Debug artifacts uploaded to Workflow run."
            )
            return []

        logger.info(f"=== Extracted Total Raw Jobs: {len(all_raw_jobs)} ===")
        return all_raw_jobs

    def enrich_descriptions(self, raw_jobs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return raw_jobs

    def run(self) -> List[Dict[str, Any]]:
        raw_cards = self.extract()
        final_jobs = self.enrich_descriptions(raw_cards)
        logger.info(f"=== Completed Wuzzuf Extraction. Total Jobs: {len(final_jobs)} ===")
        return final_jobs