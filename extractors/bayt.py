import asyncio
import json
import logging
import math
import os
import re
import time
import random
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from dotenv import load_dotenv
from parsel import Selector
from playwright.sync_api import BrowserContext, sync_playwright

from extractors.base_extractor import BaseExtractor
from core.exceptions import CriticalSelectorError
from services.alert_manager import AlertManager

load_dotenv()

logger = logging.getLogger("BaytExtractor")

def import_random_delay():
    return random.uniform(3.2, 5.8)


class BaytExtractor(BaseExtractor):

    def __init__(self, search_urls: Optional[List[str]] = None):
        super().__init__(source_name="Bayt")
        
        self.search_urls = search_urls or [
            "https://www.bayt.com/en/egypt/jobs/web-developer-jobs/",
            "https://www.bayt.com/en/egypt/jobs/data-engineer-jobs/",
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
        query_params["page"] = [str(page_num)]
        new_query = urlencode(query_params, doseq=True)
        return urlunparse((
            parsed.scheme, parsed.netloc, parsed.path,
            parsed.params, new_query, parsed.fragment
        ))

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
            last_href = selector.css('li.pagination-last-d > a::attr(href), #pagination a.jsAjaxLoad:contains("Last")::attr(href)').get()
            if not last_href:
                last_href = selector.css('a.jsAjaxLoad[pnode="last"]::attr(href), ul.pagination li.pagination-last-d a::attr(href)').get()

            if last_href:
                match = re.search(r'page=(\d+)', last_href)
                if match:
                    return int(match.group(1))

            page_numbers = []
            for btn_text in selector.css('#pagination li a::text, ul.pagination li a::text').getall():
                text = btn_text.strip()
                if text.isdigit():
                    page_numbers.append(int(text))
            if page_numbers:
                return min(max(page_numbers), 5)

            header_text = " ".join(selector.css('h1, div.card-header span ::text').getall())
            match = re.search(r'(\d+[\d,]*)\s+Jobs', header_text, re.I)
            if match:
                total_jobs = int(match.group(1).replace(',', ''))
                return min(math.ceil(total_jobs / 20), 5)
        except Exception as e:
            logger.warning(f"Could not parse total pages: {e}")
        return 1

    def _extract_job_id(self, url: str) -> str:
        match = re.search(r"jobId=(\d+)", url)
        if match:
            return match.group(1)
        match_alt = re.search(r"-(\d{6,})\b", url)
        return match_alt.group(1) if match_alt else url

    def _parse_html_to_raw_jobs(self, html_content: str) -> List[Dict[str, Any]]:
        raw_jobs = []
        selector = Selector(text=html_content)
        cards = selector.css("li.has-pointer-d")

        for card in cards:
            title_node = card.css("h2.m0t a, h2.t-large-d a, h2 a")
            if not title_node: continue

            title = title_node.css("::text").get("").strip()
            raw_link = title_node.css("::attr(href)").get("")

            # CRITICAL FIELD: title
            if not title:
                self.alert_manager.send_critical_alert(
                    source=self.source_name,
                    field="title",
                    details="Could not extract job title text from a matched job card. "
                            "The site's title selector may have changed."
                )
                raise CriticalSelectorError(source=self.source_name, field="title")

            # CRITICAL FIELD: job_link
            if not raw_link:
                self.alert_manager.send_critical_alert(
                    source=self.source_name,
                    field="job_link",
                    details=f"Could not extract job link href for job '{title}'. "
                            "The site's link structure may have changed."
                )
                raise CriticalSelectorError(source=self.source_name, field="job_link")

            if not self._is_title_allowed(title):
                continue

            job_link = raw_link if raw_link.startswith("http") else f"https://www.bayt.com{raw_link}"
            job_id = self._extract_job_id(job_link)

            # CRITICAL FIELD: company
            company = card.css("div.job-company-location-wrapper a ::text").get()
            if not company:
                company = card.css(".job-company-location-wrapper ::text").get()
            if not company:
                logger.warning(f"[{self.source_name}] Non-critical field 'company' missing for job '{title}'. Defaulting to 'N/A'.")
                company = "N/A"
            else:
                company = company.strip()

            # NON-CRITICAL FIELD: location
            location = card.css(".jb-label-location a.t-default span::text, .jb-label-location span::text").get()
            if not location:
                logger.warning(f"[{self.source_name}] Non-critical field 'location' missing for job '{title}'. Defaulting to 'N/A'.")
                location = "N/A"
            else:
                location = location.strip()

            # NON-CRITICAL FIELD: job_type
            remote_node = card.css(".jb-label-remote")
            combined_card_text = " ".join(card.css("::text").getall()).lower()

            if remote_node and "remote" in remote_node.css("::text").get("").lower():
                job_type = "Remote"
            elif "hybrid" in combined_card_text or "عمل هجين" in combined_card_text:
                job_type = "Hybrid"
            elif remote_node:
                job_type = remote_node.css("::text").get("Remote").strip()
            else:
                job_type = "On-site"

            # CRITICAL FIELD: posted_at
            posted_at = card.css('[id^="jb-date-"] ::text, .jb-label-date ::text').get("").strip()
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

            raw_jobs.append({
                "job_id": job_id,
                "title": title,
                "company": company,
                "location": location,
                "job_type": job_type,
                "job_link": job_link,
                "raw_description": "",
                "posted_at": posted_at,
                "source": self.source_name
            })
        return raw_jobs

    def enrich_descriptions(self, raw_jobs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """جلب تفاصيل الوصف مع حماية متقدمة من الـ 403 Forbidden"""
        if not raw_jobs:
            logger.info("No jobs to enrich.")
            return []

        logger.info(f" Starting Description Enrichment for {len(raw_jobs)} jobs ")

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                ]
            )
            try:
                context = browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
                    ),
                    viewport={"width": 1366, "height": 768},
                    extra_http_headers={
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                        "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
                        "Sec-Ch-Ua": '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"',
                        "Sec-Ch-Ua-Mobile": "?0",
                        "Sec-Ch-Ua-Platform": '"Windows"',
                        "Sec-Fetch-Dest": "document",
                        "Sec-Fetch-Mode": "navigate",
                        "Sec-Fetch-Site": "none",
                        "Sec-Fetch-User": "?1",
                        "Upgrade-Insecure-Requests": "1",
                    }
                )

                context.add_init_script("""
                    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                """)

                for index, job in enumerate(raw_jobs, start=1):
                    logger.info(f"[{index}/{len(raw_jobs)}] Fetching description for: {job['title']} ({job['job_link']})")
                    
                    page = context.new_page()
                    success = False

                    for attempt in range(1, 3):
                        try:
                            time.sleep(import_random_delay())
                            
                            response = page.goto(job["job_link"], wait_until="domcontentloaded", timeout=30000)

                            if response and response.status == 200:
                                page.evaluate("window.scrollTo(0, 300);")
                                html_text = page.content()
                                page_sel = Selector(text=html_text)

                                desc_node = page_sel.css("#job_card div.t-break, #job_card .card-content div.t-break")
                                if not desc_node:
                                    desc_node = page_sel.css("#view_inner .u-scrolly > div:nth-of-type(3), #view_inner .u-scrolly .t-break")
                                if not desc_node:
                                    desc_node = page_sel.css(".job-description, .t-break")

                                if desc_node:
                                    job["raw_description"] = " ".join(desc_node.css("::text").getall()).strip()
                                else:
                                    fallback_desc = page_sel.css("#job_card ::text, #view_inner .u-scrolly ::text").getall()
                                    job["raw_description"] = " ".join([t.strip() for t in fallback_desc if t.strip()])

                                # CRITICAL FIELD: description
                                if not job["raw_description"]:
                                    page.close()
                                    self.alert_manager.send_critical_alert(
                                        source=self.source_name,
                                        field="description",
                                        details=f"Page loaded (200 OK) but no description text could be extracted for "
                                                f"'{job['title']}' ({job['job_link']}). The description selector may have changed."
                                    )
                                    raise CriticalSelectorError(source=self.source_name, field="description")

                                logger.info(f" Successfully fetched description ({len(job['raw_description'])} chars).")
                                success = True
                                break
                            elif response and response.status == 403:
                                logger.warning(f" Attempt {attempt}: Received 403 Forbidden. Backing off...")
                                time.sleep(7)  # الانتظار في حالة الـ Rate Limit
                            else:
                                status_code = response.status if response else "Unknown"
                                logger.warning(f" Attempt {attempt}: Failed with status {status_code}")

                        except CriticalSelectorError:
                            raise
                        except Exception as e:
                            logger.warning(f" Attempt {attempt} Error for {job['job_link']}: {e}")

                    if not page.is_closed():
                        page.close()  
            finally:
                browser.close()

        logger.info(" Finished enriching job descriptions.")
        return raw_jobs

    def extract(self) -> List[Dict[str, Any]]:
        all_raw_jobs = []
        seen_job_ids = set()

        logger.info(f"=== Starting Bayt Extraction Process for {len(self.search_urls)} Search URLs ===")

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                ]
            )

            try:
                context = browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                        " (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
                    ),
                    viewport={"width": 1366, "height": 768},
                    extra_http_headers={
                        "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
                        "Sec-Ch-Ua": '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"',
                        "Sec-Ch-Ua-Mobile": "?0",
                        "Sec-Ch-Ua-Platform": '"Windows"',
                        "Sec-Fetch-Dest": "document",
                        "Sec-Fetch-Mode": "navigate",
                        "Sec-Fetch-Site": "same-origin",
                        "Sec-Fetch-User": "?1",
                        "Upgrade-Insecure-Requests": "1",
                    }
                )

                page = context.new_page()

                for url_index, search_url in enumerate(self.search_urls, start=1):
                    logger.info(f"\n>>> Processing Search URL [{url_index}/{len(self.search_urls)}]: {search_url}")
                    
                    first_page_url = self._build_page_url(search_url, 1)
                    logger.info(f"Opening first page: {first_page_url}")

                    try:
                        page.goto(first_page_url, wait_until="domcontentloaded", timeout=40000)
                        page.wait_for_selector("li.has-pointer-d", timeout=20000)
                    except Exception as e:
                        logger.warning(f"Timeout or error loading first page ({first_page_url}): {e}")

                    first_html = page.content()
                    first_selector = Selector(text=first_html)
                    total_pages = self._get_total_pages(first_selector)
                    logger.info(f"Total pages detected for this URL: {total_pages}")

                    for page_num in range(1, total_pages + 1):
                        current_url = self._build_page_url(search_url, page_num)
                        logger.info(f"--- Scraping Page {page_num} of {total_pages} | URL: {current_url} ---")

                        if page_num > 1:
                            try:
                                time.sleep(2.5)
                                page.goto(current_url, wait_until="domcontentloaded", timeout=30000)
                                page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2);")
                                time.sleep(1)
                                page.evaluate("window.scrollTo(0, document.body.scrollHeight);")
                                time.sleep(1)
                                page.wait_for_selector("li.has-pointer-d", timeout=15000)
                                html_content = page.content()
                            except Exception as e:
                                logger.warning(f"Error loading page {page_num}: {e}")
                                try:
                                    html_content = page.content()
                                except Exception:
                                    html_content = ""
                        else:
                            page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2);")
                            time.sleep(0.5)
                            html_content = page.content()

                        if html_content:
                            raw_jobs = self._parse_html_to_raw_jobs(html_content)
                            page_jobs_added = 0
                            
                            for job in raw_jobs:
                                if job["job_id"] not in seen_job_ids:
                                    seen_job_ids.add(job["job_id"])
                                    all_raw_jobs.append(job)
                                    page_jobs_added += 1

                            logger.info(
                                f"Page {page_num} Summary: Extracted {len(raw_jobs)} valid jobs "
                                f"({page_jobs_added} new unique jobs added to total list)."
                            )
                        else:
                            logger.warning(f"No HTML content retrieved for page {page_num}.")
            finally:
                browser.close()

        logger.info(f"\n=== Bayt Scraping Finished ===")
        logger.info(f"Total Unique Jobs Extracted Across All URLs: {len(all_raw_jobs)}")
        
        return all_raw_jobs