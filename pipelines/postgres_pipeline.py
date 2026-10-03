import logging
from typing import Any, Dict, List
from psycopg2.extras import execute_values
from config.database import get_db_connection  

logger = logging.getLogger(__name__)


class PostgresPipeline:

    def filter_uncached_jobs(
        self, source: str, raw_jobs: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        It receives the raw jobs extracted from the list,
        immediately excludes any job_id present in the cache table for this source,
        and returns only completely new jobs.
        """
        if not raw_jobs:
            return []

        job_ids = [str(job["job_id"]) for job in raw_jobs]

        query = """
            SELECT job_id
            FROM processed_jobs_cache
            WHERE source = %s AND job_id = ANY(%s);
        """

        cached_ids = set()
        try:
            with get_db_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(query, (source, job_ids))
                    rows = cursor.fetchall()
                    cached_ids = {str(row[0]) for row in rows}
        except Exception as e:
            logger.error(
                f"[{source}] Error querying DB cache, proceeding with all raw jobs: {e}"
            )
            return raw_jobs

        uncached_jobs = [
            job for job in raw_jobs if str(job["job_id"]) not in cached_ids
        ]

        logger.info(
            f"[{source}] Cache Check: {len(cached_ids)} jobs skipped from cache. "
            f"{len(uncached_jobs)} NEW jobs passed for AI extraction."
        )

        return uncached_jobs

    def mark_as_processed(self, source: str, job_ids: List[str]):
        """
        Mark jobs as processed by adding their IDs to the cache table.
        """
        if not job_ids:
            return

        query = """
            INSERT INTO processed_jobs_cache (source, job_id)
            VALUES %s
            ON CONFLICT (source, job_id) DO NOTHING;
        """

        records = [(source, str(j_id)) for j_id in job_ids]

        try:
            with get_db_connection() as conn:
                with conn.cursor() as cursor:
                    execute_values(cursor, query, records)
                    conn.commit()
                    logger.info(
                        f"[{source}] Successfully cached {len(records)} job IDs."
                    )
        except Exception as e:
            logger.error(f"[{source}] Error saving job IDs to cache: {e}")

    def save_verified_jobs(self, verified_jobs: List[Dict[str, Any]]):
        """
        Saves the verified jobs to the 'jobs' table in PostgreSQL.
        If a job with the same source and job_id already exists, it updates the existing record"""
        
        if not verified_jobs:
            return

        query = """
            INSERT INTO jobs (source, job_id, title, company, location, job_type, job_link, category, description, summary, posted_at)
            VALUES %s
            ON CONFLICT (source, job_id) 
            DO UPDATE SET 
                title = EXCLUDED.title,
                company = EXCLUDED.company,
                location = EXCLUDED.location,
                job_type = EXCLUDED.job_type,
                category = EXCLUDED.category,
                description = EXCLUDED.description,
                summary = EXCLUDED.summary,
                posted_at = EXCLUDED.posted_at,
                scraped_at = CURRENT_TIMESTAMP;
        """

        records = []
        for job in verified_jobs:
            records.append((
                job.get("source"),
                str(job.get("job_id")),
                job.get("title"),
                job.get("company"),
                job.get("location"),
                job.get("job_type"),
                job.get("job_link"),
                job.get("category"),
                job.get("raw_description", job.get("description", "")),
                job.get("description", ""),  # AI Summary
                job.get("posted_at"),
            ))

        try:
            with get_db_connection() as conn:
                with conn.cursor() as cursor:
                    execute_values(cursor, query, records)
                    conn.commit()
                    logger.info(
                        f"Successfully saved/updated {len(records)} verified jobs in 'jobs' table."
                    )
        except Exception as e:
            logger.error(f"Error saving verified jobs to 'jobs' table: {e}")


    def filter_unpublished_jobs(
        self, source: str, jobs: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Excludes jobs already posted to the public Telegram channel for this
        source, based on the published_jobs_cache table.
        """
        if not jobs:
            return []

        job_ids = [str(job["job_id"]) for job in jobs]

        query = """
            SELECT job_id
            FROM published_jobs_cache
            WHERE source = %s AND job_id = ANY(%s);
        """

        already_published = set()
        try:
            with get_db_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(query, (source, job_ids))
                    rows = cursor.fetchall()
                    already_published = {str(row[0]) for row in rows}
        except Exception as e:
            logger.error(
                f"[{source}] Error querying published_jobs_cache, proceeding with all jobs: {e}"
            )
            return jobs

        unpublished = [job for job in jobs if str(job["job_id"]) not in already_published]

        logger.info(
            f"[{source}] Publish Cache Check: {len(already_published)} job(s) already published. "
            f"{len(unpublished)} new job(s) queued for publishing."
        )

        return unpublished

    def mark_as_published(self, source: str, job_ids: List[str]):
        """
        Records job_ids successfully posted to the public Telegram channel
        so they are never published again.
        """
        if not job_ids:
            return

        query = """
            INSERT INTO published_jobs_cache (source, job_id)
            VALUES %s
            ON CONFLICT (source, job_id) DO NOTHING;
        """

        records = [(source, str(j_id)) for j_id in job_ids]

        try:
            with get_db_connection() as conn:
                with conn.cursor() as cursor:
                    execute_values(cursor, query, records)
                    conn.commit()
                    logger.info(f"[{source}] Marked {len(records)} job(s) as published.")
        except Exception as e:
            logger.error(f"[{source}] Error saving published job IDs: {e}")


if __name__ == "__main__":
    pipeline = PostgresPipeline()
    print("PostgresPipeline loaded successfully with Clean Deduplication Logic!")