class CriticalSelectorError(Exception):
    """
    Raised when a critical field's selector fails to extract data,
    indicating the source website's UI/HTML structure has likely changed.
    Raising this halts scraping for the offending source only; the
    orchestrator catches it and moves on to the next source.
    """

    def __init__(self, source: str, field: str, message: str = ""):
        self.source = source
        self.field = field
        self.message = message or (
            f"Critical selector failure for field '{field}' in source '{source}'. "
            "Site structure may have changed."
        )
        super().__init__(self.message)