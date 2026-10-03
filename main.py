import logging
from typing import List
from core.exceptions import CriticalSelectorError
from extractors.base_extractor import BaseExtractor
from extractors.bayt import BaytExtractor
from extractors.wuzzuf import WuzzufExtractor
from pipelines.postgres_pipeline import PostgresPipeline
from services.ai_service import AIVerificationService
from services.telegram_publisher import TelegramPublisher

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("PipelineOrchestrator")


class JobPipelineOrchestrator:

    def __init__(self):
        self.pipeline = PostgresPipeline()
        self.ai_service = AIVerificationService()
        self.telegram_publisher = TelegramPublisher(pipeline=self.pipeline)

    def run_extractor(self, extractor: BaseExtractor):
        source = extractor.source_name
        logger.info(f"START PIPELINE FOR SOURCE: {source}")

        # 1. Extraction (Raw Jobs)
        raw_jobs = extractor.run()
        if not raw_jobs:
            logger.info(f"[{source}] No raw jobs extracted. Skipping pipeline.")
            return

        # 2. Database Deduplication Filter (Cache Check)
        uncached_jobs = self.pipeline.filter_uncached_jobs(source=source, raw_jobs=raw_jobs)
        if not uncached_jobs:
            logger.info(f"[{source}] All jobs are cached. Skipping AI & Detail Extraction.")
            return

        # 3. Description Enrichment 
        if hasattr(extractor, "enrich_descriptions"):
            uncached_jobs = extractor.enrich_descriptions(uncached_jobs)

        # 4. AI Verification & Processing
        verified_jobs = self.ai_service.verify_and_enrich_jobs(uncached_jobs)

        # 5. Save Cache 
        processed_ids = [str(job["job_id"]) for job in uncached_jobs]
        self.pipeline.mark_as_processed(source=source, job_ids=processed_ids)

        # 6. Save Verified Jobs to Main Table
        if verified_jobs:
            self.pipeline.save_verified_jobs(verified_jobs)

        # 7. Publish newly verified jobs to the Public Audience Telegram channel
        if verified_jobs:
            self.telegram_publisher.publish_verified_jobs(source=source, verified_jobs=verified_jobs)

        logger.info(
            f"[{source}] Pipeline finished. "
            f"Uncached: {len(uncached_jobs)} | Verified & Saved: {len(verified_jobs)}"
        )

    def run_all(self, extractors: List[BaseExtractor]):
        for extractor in extractors:
            try:
                self.run_extractor(extractor)
            except CriticalSelectorError as e:
                logger.error(
                    f"[HALTED] Critical selector failure for source '{extractor.source_name}' "
                    f"(field: '{e.field}'). An admin alert has already been dispatched. "
                    f"Skipping this source and continuing with the next one."
                )
            except Exception as e:
                logger.error(f"Failed execution for extractor {extractor.source_name}: {e}", exc_info=True)


if __name__ == "__main__":
    extractors_to_run = [
        BaytExtractor(
            search_urls=[
                "https://www.bayt.com/en/egypt/jobs/web-developer-jobs/",
                "https://www.bayt.com/en/egypt/jobs/data-engineer-jobs/",
            ]
        ),
        WuzzufExtractor(
            search_urls=[
                "https://wuzzuf.net/search/jobs/?q=web+developer&a=hpb",
                "https://wuzzuf.net/search/jobs/?q=data+engineer&a=hpb",
               
            ]
        ),
        # WuzzufExtractor(),
        # RemotiveExtractor(),
    ]

    orchestrator = JobPipelineOrchestrator()
    orchestrator.run_all(extractors_to_run)