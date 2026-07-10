"""Environment/config loading, shared across all modules."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

SERP_API_KEY = os.getenv("SERP_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

CLASSIFIER_MODEL = os.getenv("CLASSIFIER_MODEL", "gpt-5.2")
CLASSIFY_BATCH_SIZE = int(os.getenv("CLASSIFY_BATCH_SIZE", "10"))
CLASSIFY_CONCURRENCY = int(os.getenv("CLASSIFY_CONCURRENCY", "3"))
# How many restaurants to analyze in parallel in a batch report
BUSINESS_CONCURRENCY = int(os.getenv("BUSINESS_CONCURRENCY", "2"))
MAX_REVIEWS_DEFAULT = int(os.getenv("MAX_REVIEWS_DEFAULT", "100"))


def missing_keys() -> list[str]:
    missing = []
    if not SERP_API_KEY:
        missing.append("SERP_API_KEY")
    if not OPENAI_API_KEY:
        missing.append("OPENAI_API_KEY")
    return missing
