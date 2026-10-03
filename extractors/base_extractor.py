import logging
from abc import ABC, abstractmethod
from typing import List, Dict, Any

# Configure logging for the extractors module
logger = logging.getLogger(__name__)


class BaseExtractor(ABC):
    """
    Abstract Base Class for all data extractors (APIs, RSS feeds, and Web Scrapers).
    Enforces a unified structure across all source-specific scripts.
    """

    def __init__(self, source_name: str):
        """
        Initializes the extractor with a source name.
        
        :param source_name: The name of the platform (e.g., 'Wuzzuf', 'Remotive').
        """
        self.source_name = source_name
        self.logger = logging.getLogger(self.__class__.__name__)
    @abstractmethod
    def extract(self) -> List[Dict[str, Any]]:
        """
        Abstract method that must be implemented by every subclass.
        Should fetch raw data from the source and return a list of standardized dictionaries.

        :return: A list of dictionaries representing raw job items.
        """
        pass

    def run(self) -> List[Dict[str, Any]]:
        """
        Safely executes the extraction process with standardized error handling and logging.

        :return: List of extracted raw job items or an empty list if an error occurs.
        """
        logger.info(f"Starting data extraction from source: {self.source_name}")
        try:
            items = self.extract()
            logger.info(f"Successfully extracted {len(items)} items from {self.source_name}")
            return items
        except Exception as e:
            logger.error(f"Error occurred while extracting from {self.source_name}: {e}", exc_info=True)
            return []




if __name__ == "__main__":
    # Test subclass implementation
    class DummyExtractor(BaseExtractor):
        def extract(self) -> List[Dict[str, Any]]:
            return [{"title": "Data Engineer", "company": "Test Co"}]

    dummy = DummyExtractor(source_name="Test Source")
    results = dummy.run()
    print("Test Output:", results)