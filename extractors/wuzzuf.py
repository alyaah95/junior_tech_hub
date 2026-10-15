import asyncio
import logging
import math
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

    def _apply_stealth(self, page):
        if stealth_sync:
            try:
                stealth_sync(page)
            except Exception as e:
                logger.debug(f"Could not apply stealth_sync: {e}")
        
        page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en', 'ar'] });
            window.chrome = { runtime: {}, loadTimes: function() {}, csi: function() {}, app: {} };
        """)

    def _block_unnecessary_resources(self, route):
        if route.request.resource_type in ["image", "media", "font"]:
            route.abort()
        else:
            route.continue_()

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

    def _handle_cloudflare(self, page, wait_ms: int = 10000) -> bool:
        try:
            title = page.title()
        except Exception:
            return False

        if any(term in title for term in ["Just a moment", "Cloudflare", "Attention Required", "Checking your browser"]):
            logger.warning("Cloudflare challenge detected! Waiting for bypass...")
            page.wait_for_timeout(wait_ms)
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            return True
        return False

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
                "//strong[contains(text(), '')]/text() | "
                "//span[contains(@class, 'css-')]/strong/text() | "
                "//p//strong/text()"
            ).get()

            if jobs_count_text:
                clean_num = re.sub(r'[^\d]', '', jobs_count_text)
                if clean_num:
                    total_jobs = int(clean_num)
                    calculated_pages = math.ceil(total_jobs / 15)
                    return min(calculated_pages, 5)

            page_numbers = selector.xpath("//ul//li//button//text() | //ul//li//a//text()").getall()
            valid_pages = [int(p.strip()) for p in page_numbers if p.strip().isdigit()]
            if valid_pages:
                return min(max(valid_pages), 5)

        except Exception as e:
            logger.warning(f"Could not parse total pages: {e}")

        return 1

    def _extract_job_id(self, url: str) -> str:
        match = re.search(r"/jobs/p/([a-zA-Z0-9-]+)", url)
        if match:
            return match.group(1)
        return url

    def _parse_html_to_raw_jobs(self, html_content: str) -> List[Dict[str, Any]]:
        raw_jobs = []
        selector = Selector(text=html_content)
        
        job_links = selector.xpath("//a[contains(@href, '/jobs/p/')]")

        for link_node in job_links:
            raw_link = link_node.xpath(".//@href").get("")
            title = link_node.xpath(".//text()").get("").strip()

            # CRITICAL FIELD: job_link
            if not raw_link:
                self.alert_manager.send_critical_alert(
                    source=self.source_name,
                    field="job_link",
                    details="Could not extract 'href' from a job card anchor matching '/jobs/p/'. "
                            "The site's link structure may have changed."
                )
                raise CriticalSelectorError(source=self.source_name, field="job_link")

            # CRITICAL FIELD: title
            if not title or len(title) < 3:
                self.alert_manager.send_critical_alert(
                    source=self.source_name,
                    field="title",
                    details=f"Could not extract a valid title for job link '{raw_link}'. "
                            "The site's title selector may have changed."
                )
                raise CriticalSelectorError(source=self.source_name, field="title")

            if not self._is_title_allowed(title):
                continue

            job_link = raw_link if raw_link.startswith("http") else f"https://wuzzuf.net{raw_link}"
            job_id = self._extract_job_id(job_link)

            card = link_node.xpath(".//a[contains(@href, '/jobs/p/')]/ancestor::div[contains(@class, 'css-')][2]")
            if not card:
                card = link_node.xpath("./ancestor::div[2]")

            # NON-CRITICAL FIELD: company
            company = card.xpath(".//a[contains(@href, '/jobs/careers/')]/text()").get()
            if not company:
                logger.warning(f"[{self.source_name}] Non-critical field 'company' missing for job '{title}'. Defaulting to 'N/A'.")
                company = "N/A"
            else:
                company = company.strip()

            # NON-CRITICAL FIELD: location
            loc_parts = card.xpath(".//a[contains(@href, '/jobs/careers/')]/following-sibling::span//text() | .//span[contains(text(), 'Egypt') or contains(text(), 'Cairo') or contains(text(), 'Alexandria')]//text()").getall()
            location = ", ".join([p.strip() for p in loc_parts if p.strip() and p.strip() != "-"]) if loc_parts else ""
            if not location:
                logger.warning(f"[{self.source_name}] Non-critical field 'location' missing for job '{title}'. Defaulting to 'N/A'.")
                location = "N/A"

            # NON-CRITICAL FIELD: job_type
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

            # CRITICAL FIELD: posted_at
            posted_at = card.xpath(".//div[contains(text(), 'ago') or contains(text(), 'just now')]/text()").get("").strip()
            if not posted_at:
                self.alert_manager.send_critical_alert(
                    source=self.source_name,
                    field="posted_at",
                    details=f"Could not extract 'posted_at' for job '{title}' ({job_link}). "
                            "The site's date selector may have changed."
                )
                raise CriticalSelectorError(source=self.source_name, field="posted_at")

            if not self.is_recently_posted(posted_at):
                continue

            card_texts = card.xpath(".//text()").getall()
            clean_card_text = " ".join([t.strip() for t in card_texts if t.strip()])

            # CRITICAL FIELD: description
            if not clean_card_text:
                self.alert_manager.send_critical_alert(
                    source=self.source_name,
                    field="description",
                    details=f"Could not extract any card text for job '{title}' ({job_link}). "
                            "The card's text-level selector may have changed."
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

    def extract(self) -> List[Dict[str, Any]]:
        all_raw_jobs = []
        seen_job_ids = set()

        logger.info(f"=== Starting Wuzzuf Scraping for {len(self.search_urls)} Search URLs ===")

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-infobars",
                    "--window-size=1366,768"
                ]
            )

            try:
                context = browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                    viewport={"width": 1366, "height": 768},
                    locale="en-US",
                    timezone_id="Africa/Cairo"
                )
                
                page = context.new_page()
                # page.route("**/*", self._block_unnecessary_resources)
                self._apply_stealth(page)

                for url_index, search_url in enumerate(self.search_urls, start=1):
                    if url_index > 1:
                        time.sleep(get_random_delay(3.0, 5.0))

                    first_page_url = self._build_page_url(search_url, 1)
                    try:
                        page.goto(first_page_url, wait_until="load", timeout=60000)
                        self._handle_cloudflare(page)
                        self._simulate_human_behavior(page)

                        try:
                            page.wait_for_selector("a[href*='/jobs/p/']", timeout=20000)
                        except Exception:
                            if self._handle_cloudflare(page):
                                try:
                                    page.wait_for_selector("a[href*='/jobs/p/']", timeout=15000)
                                except Exception:
                                    pass
                    except Exception as e:
                        logger.warning(f"Error loading first page ({first_page_url}): {e}")

                    first_html = ""
                    try:
                        page.wait_for_load_state("domcontentloaded", timeout=5000)
                        first_html = page.content()
                    except Exception:
                        try:
                            first_html = page.content()
                        except Exception:
                            first_html = ""

                    first_selector = Selector(text=first_html)
                    total_pages = self._get_total_pages(first_selector)

                    for page_num in range(1, total_pages + 1):
                        current_url = self._build_page_url(search_url, page_num)

                        if page_num > 1:
                            try:
                                time.sleep(get_random_delay(2.5, 4.0))
                                page.goto(current_url, wait_until="domcontentloaded", timeout=45000)
                                self._handle_cloudflare(page)
                                self._simulate_human_behavior(page)

                                try:
                                    page.wait_for_selector("a[href*='/jobs/p/']", timeout=25000)
                                except Exception:
                                    if self._handle_cloudflare(page):
                                        try:
                                            page.wait_for_selector("a[href*='/jobs/p/']", timeout=15000)
                                        except Exception:
                                            pass

                                page.wait_for_load_state("domcontentloaded", timeout=5000)
                                html_content = page.content()
                            except Exception as e:
                                time.sleep(2)
                                try:
                                    self._handle_cloudflare(page)
                                    html_content = page.content()
                                except Exception:
                                    html_content = ""
                        else:
                            html_content = first_html

                        if html_content:
                            raw_jobs = self._parse_html_to_raw_jobs(html_content)

                            if not raw_jobs and page_num > 1:
                                if self._handle_cloudflare(page):
                                    time.sleep(get_random_delay(2.0, 3.0))
                                    try:
                                        page.reload(wait_until="load", timeout=45000)
                                        self._handle_cloudflare(page)
                                        page.wait_for_selector("a[href*='/jobs/p/']", timeout=15000)
                                        html_content = page.content()
                                        raw_jobs = self._parse_html_to_raw_jobs(html_content)
                                    except Exception:
                                        pass

                                if not raw_jobs:
                                    break

                            page_jobs_added = 0
                            for job in raw_jobs:
                                if job["job_id"] not in seen_job_ids:
                                    seen_job_ids.add(job["job_id"])
                                    all_raw_jobs.append(job)
                                    page_jobs_added += 1

                            logger.info(f"URL [{url_index}/{len(self.search_urls)}] | Page {page_num}/{total_pages}: Extracted {page_jobs_added} new jobs.")

            finally:
                browser.close()

        logger.info(f"=== Extracted Total Raw Jobs: {len(all_raw_jobs)} ===")
        return all_raw_jobs

    def enrich_descriptions(self, raw_jobs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return raw_jobs

    def run(self) -> List[Dict[str, Any]]:
        raw_cards = self.extract()
        final_jobs = self.enrich_descriptions(raw_cards)
        logger.info(f"=== Completed Wuzzuf Extraction. Total Jobs: {len(final_jobs)} ===")
        return final_jobs