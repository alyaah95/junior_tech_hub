import re
from typing import Optional
from datetime import datetime
from pydantic import BaseModel, Field, field_validator
from bs4 import BeautifulSoup


class JobSchema(BaseModel):
    job_id: str = Field(..., description="Unique job identifier per source, e.g., wuzzuf_12345 or bayt_98765")
    source: str = Field(..., description="Source platform name, e.g., Wuzzuf, Bayt")
    title: str = Field(..., description="Job title, e.g., Data Engineer")
    company: str = Field(..., description="Company name")
    location: str = Field(..., description="Job location, e.g., Cairo, Egypt or Remote")
    job_link: str = Field(..., description="Unique URL for the job post")

    # Optional Fields
    job_type: Optional[str] = Field(default=None, description="Employment type, e.g., Full-time, Hybrid, Remote")
    category: Optional[str] = Field(default="General Software Engineering", description="Technical domain category")
    description: Optional[str] = Field(default=None, description="Raw job description extracted from site")
    summary: Optional[str] = Field(default=None, description="Short AI-generated summary of key tech stack")
    posted_at: Optional[str] = Field(default=None, description="Original posting date string")
    scraped_at: Optional[datetime] = Field(default=None, description="Auto-handled by PostgreSQL DEFAULT CURRENT_TIMESTAMP")

    # Validators for Data Cleaning
    @field_validator("title", "company", "location", mode="before")
    @classmethod
    def clean_required_text_fields(cls, value: str) -> str:
        """
        Cleans required text fields. Ensures value never evaluates to empty/None.
        """
        if not value or not str(value).strip():
            raise ValueError("Field cannot be empty")

        soup = BeautifulSoup(str(value), "html.parser")
        text = soup.get_text(separator=" ")
        clean_value = re.sub(r"\s+", " ", text).strip()

        if not clean_value:
            raise ValueError("Field cannot be empty after HTML stripping")
        return clean_value

    @field_validator("job_type", "category", "description", "summary", "posted_at", mode="before")
    @classmethod
    def clean_optional_text_fields(cls, value: Optional[str]) -> Optional[str]:
        """
        Cleans optional text fields safely without breaking Optional typing.
        """
        if not value:
            return None

        soup = BeautifulSoup(str(value), "html.parser")
        text = soup.get_text(separator=" ")
        clean_value = re.sub(r"\s+", " ", text).strip()
        return clean_value if clean_value else None

    @field_validator("job_link", mode="before")
    @classmethod
    def clean_job_link(cls, value: str) -> str:
        """
        Ensures the job link is a clean string without extra spaces.
        """
        if not value or not str(value).strip():
            raise ValueError("job_link cannot be empty")
        return str(value).strip()