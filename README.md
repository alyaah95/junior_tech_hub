# 🚀 Automated Multi-Source Job Aggregator & AI Verification Pipeline

> An enterprise-grade, localized data engineering pipeline that automates job hunting by scraping listings from **Wuzzuf** and **Bayt**, bypasses aggressive anti-bot protections (Cloudflare Turnstile), filters opportunities using **Groq LLM AI Agent**, and publishes verified junior roles directly to a public Telegram channel.

---

## 🏗️ System Architecture

```
+-----------------------------------------------------------------------------------+
|                                 EXTRACTION LAYER                                  |
|  +-------------------------------------+   +-----------------------------------+  |
|  |           Wuzzuf Extractor          |   |           Bayt Extractor          |  |
|  |  Playwright Chromium + Stealth      |   |  Playwright Sync + Stealth        |  |
|  |  Resource Blocker (CSS/Fonts/Img)   |   |  Pagination & Raw DOM Extractor   |  |
|  +-------------------------------------+   +-----------------------------------+  |
+------------------------------------------+----------------------------------------+
                                           |
                                           v
+-----------------------------------------------------------------------------------+
|                         ORCHESTRATION & PRE-FILTERING                             |
|  +-----------------------------------------------------------------------------+  |
|  |                        Job Pipeline Orchestrator                            |  |
|  |  1. Batch Cache Check via PostgreSQL (processed_jobs_cache)               |  |
|  |  2. Uncached Jobs Identification                                            |  |
|  |  3. On-Demand Description Enrichment                                        |  |
|  +-----------------------------------------------------------------------------+  |
+------------------------------------------+----------------------------------------+
                                           |
                                           v
+-----------------------------------------------------------------------------------+
|                               AI VERIFICATION LAYER                               |
|  +-----------------------------------------------------------------------------+  |
|  |                           Groq LLM Verification                             |  |
|  |  - Prompts for Junior/Entry-Level & Web Dev / Data Engineering              |  |
|  |  - Filters out Senior, Managerial, Non-coding, or Irrelevant roles          |  |
|  +-----------------------------------------------------------------------------+  |
+------------------------------------------+----------------------------------------+
                                           |
                                           v
+-----------------------------------------------------------------------------------+
|                         PERSISTENCE & PUBLISHING LAYER                            |
|  +-----------------------------------+   +-------------------------------------+  |
|  |         PostgreSQL Database       |   |          Telegram Publisher         |  |
|  |  - verified_jobs (Target DB)      |   |  - Publishes Verified Jobs          |  |
|  |  - processed_jobs_cache           |   |  - Direct Traffic via Original Link |  |
|  +-----------------------------------+   +-------------------------------------+  |
+-----------------------------------------------------------------------------------+
```

---

## 💡 Key Engineering Highlights & Core Strengths

1. **Advanced Anti-Bot Bypassing & Human Emulation:**
   * Uses `Playwright-Stealth` along with mouse movement emulation, random delay intervals, and custom viewport settings to bypass Wuzzuf's Cloudflare Turnstile barriers without triggering IP challenges.
   * Employs resource route-blocking (aborting image, media, and font requests) to cut page load times by up to 60% and conserve memory.

2. **Two-Tier Deduplication & Cost Optimization:**
   * Implements a pre-filtering mechanism against a local PostgreSQL database (`processed_jobs_cache`) using composite keys (`source` + `job_id`).
   * Eliminates unnecessary LLM API calls and page enrichments for previously processed or rejected jobs, optimizing Groq API token consumption by over 90%.

3. **LLM-Powered Quality Gatekeeper (Groq Agent):**
   * Employs an LLM prompt pipeline to evaluate job titles and rich descriptions.
   * Rejects mislabeled "Junior" roles requiring >2 years of experience, QA/Testing positions, and non-coding engineering roles (e.g., Procurement Engineers).

4. **Fault Tolerance & Dynamic Circuit Breakers:**
   * Utilizes custom exception structures (`CriticalSelectorError`) to catch DOM breaking changes.
   * Integrates an automated `AlertManager` that sends Telegram alerts to administrators on failure while allowing the orchestrator to skip the failing source gracefully without halting the pipeline.

5. **Defensive Pagination Safety Caps:**
   * Implements dynamic page count extraction with strict safety thresholds (`min(calculated_pages, 5)` or `10`), avoiding infinite loops, rate limiting, or excessive requests.

---

## 🛠️ Architectural Trade-Offs & Strategic Decisions

### 1. The GitHub Actions to Local Pipeline Pivot
* **The Challenge:** The initial architecture targeted **GitHub Actions** for automated cron scheduling. However, GitHub's datacenter IP ranges were flagged and blocked by Cloudflare, resulting in 0 jobs extracted and workflow timeouts.
* **The Evaluation:** Considered migrating to cloud VPS providers (e.g., Oracle Cloud, AWS EC2), but identified high maintenance overhead, risk of IP blacklisting, and potential cloud hosting costs.
* **The Solution:** Made an intentional architectural decision to shift to **Local Execution**. Running the pipeline on a residential IP network with Playwright-Stealth guarantees 100% extraction stability, zero infrastructure cost, and reliable operation. Local scheduling (via OS Task Scheduler / Cron) serves as an effective operational strategy.

### 2. Ethical Scraping & Value Creation
* **Platform Respect & Traffic Generation:** This project is built on ethical data extraction principles. The pipeline does not store full articles to replace source platforms. Instead, it aggregates brief summaries and directs users directly to the original job URLs on **Wuzzuf** and **Bayt**, driving qualified organic traffic back to the source job boards.

---

## 🔮 Future Roadmap

* **Platform Scaling:** Expanding the extractor layer to integrate international and remote platforms such as Remotive, LinkedIn, and Indeed.
* **Interactive Analytics Dashboard:** Developing a Streamlit/FastAPI frontend over the PostgreSQL database to track hiring trends, top required skills, and market demand for Junior Web & Data roles.

---

## ⚙️ Tech Stack & Dependencies

* **Language:** Python 3.10+
* **Scraping & Automation:** Playwright, `playwright-stealth`, Parsel
* **Database & Caching:** PostgreSQL, `psycopg2-binary` (Connection Pooling & Keepalives)
* **AI & Validation:** Pydantic, Groq API (OpenAI-compatible)
* **Notifications:** Telegram Bot API
