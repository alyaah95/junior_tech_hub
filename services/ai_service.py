import json
import os
import time
import logging
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv
from groq import Groq

load_dotenv()
logger = logging.getLogger("AIService")

class AIVerificationService:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("GROQ_API_KEY")
        self.client = Groq(api_key=self.api_key) if self.api_key else None

    def verify_and_enrich_jobs(self, raw_jobs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not raw_jobs or not self.client:
            logger.warning("No jobs to verify or Groq API client is not configured.")
            return []

        verified_jobs = []
        batch_size = 5
        
        total_input_jobs = len(raw_jobs)
        logger.info(f"=== [AI Verification Started] Total Input Jobs Received: {total_input_jobs} ===")

        candidate_models = ["openai/gpt-oss-120b", "qwen/qwen3.6-27b", "openai/gpt-oss-20b"]

        for i in range(0, len(raw_jobs), batch_size):
            batch = raw_jobs[i : i + batch_size]
            minified_batch = [
                {
                    "id": idx,
                    "title": j.get("title", ""),
                    "details": j.get("raw_description", "")[:1200],  
                }
                for idx, j in enumerate(batch)
            ]

            prompt = f"""
You are an expert Data Pipeline AI for an Egyptian Tech Job Portal.
Analyze the following job listings and verify if they are suitable for Junior/Fresh Graduates/Entry-Level engineers in Web Development or Data Engineering.

VALIDATION RULES:
1. `is_junior_eligible`: Set to `true` ONLY if it is Student, Entry-Level, Fresh Graduate, Associate, or requires 0-3 years of experience. Reject Senior/Lead/Architect/Manager positions.
2. `category`:
   - "Web Development": Frontend, Backend, Fullstack, Web Engineer.
   - "Data": Data Engineer, PySpark/ETL Developer, Data Pipeline Engineer, Lakehouse/Data Warehouse Engineer.
   - "Other": Exclude non-coding, Data Analyst, BI, Data Scientist, DevOps, Support, QA.
3. `summary`: Extremely concise summary (MAX 15 words) highlighting key tech stack.
4. `rejection_reason`: If `is_junior_eligible` is false or `category` is "Other", explain WHY in 1 short sentence (e.g., "Requires Senior level 5+ years experience" or "Role is Data Analyst, not Data Engineering"). If approved, leave it as null.

JOBS:
{json.dumps(minified_batch, ensure_ascii=False)}

OUTPUT FORMAT (JSON Only):
{{
  "results": [
    {{
      "id": 0,
      "is_junior_eligible": true,
      "category": "Web Development",
      "summary": "Concise tech stack summary",
      "rejection_reason": null
    }}
  ]
}}
"""
            response_content = None

            for model_name in candidate_models:
                try:
                    response = self.client.chat.completions.create(
                        model=model_name,
                        messages=[
                            {"role": "system", "content": "You are a strict data classification API that returns valid JSON."},
                            {"role": "user", "content": prompt},
                        ],
                        temperature=0.1,
                        response_format={"type": "json_object"},
                    )
                    response_content = response.choices[0].message.content
                    break
                except Exception as e:
                    logger.warning(f"Batch error with model {model_name}: {e}. Retrying...")

            if not response_content:
                logger.error(f"Failed to get response from AI for Batch {i//batch_size + 1}")
                continue

            try:
                parsed_data = json.loads(response_content)
                results = parsed_data.get("results") if isinstance(parsed_data, dict) else parsed_data

                if isinstance(results, list):
                    for res in results:
                        idx = res.get("id")
                        if idx is not None and 0 <= idx < len(batch):
                            original_job = batch[idx]
                            job_title = original_job.get("title", "Unknown Title")
                            
                            is_eligible = res.get("is_junior_eligible")
                            category = res.get("category")
                            rejection_reason = res.get("rejection_reason", "No reason provided")

                            if is_eligible is True and category in ["Web Development", "Data"]:
                                logger.info(f"✅ [APPROVED] Job: '{job_title}' | Category: {category}")
                                verified_jobs.append({
                                    "job_id": original_job.get("job_id"),
                                    "title": original_job.get("title"),
                                    "company": original_job.get("company"),
                                    "location": original_job.get("location"),
                                    "job_type": original_job.get("job_type"),
                                    "job_link": original_job.get("job_link"),
                                    "source": original_job.get("source"),
                                    "category": category,
                                    "raw_description": original_job.get("raw_description", ""),
                                    "description": res.get("summary"),
                                    "posted_at": original_job.get("posted_at"),
                                })
                            else:
                                logger.warning(
                                    f"❌ [REJECTED] Job: '{job_title}' | "
                                    f"Junior: {is_eligible} | Category: {category} | Reason: {rejection_reason}"
                                )
                time.sleep(2.0)
            except Exception as e:
                logger.error(f"Error parsing batch response: {e}")

        logger.info(
            f"=== [AI Verification Finished] "
            f"Total Input: {total_input_jobs} | Total Verified Output: {len(verified_jobs)} | "
            f"Rejection Rate: {((total_input_jobs - len(verified_jobs)) / total_input_jobs * 100):.1f}% ==="
        )

        return verified_jobs