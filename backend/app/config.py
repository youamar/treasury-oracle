import os
from dotenv import load_dotenv

load_dotenv()

CHUTES_API_KEY = os.getenv("CHUTES_API_KEY", "")
CHUTES_API_KEY_FALLBACK = os.getenv("CHUTES_API_KEY_FALLBACK", "")
CHUTES_BASE_URL = os.getenv("CHUTES_BASE_URL", "https://llm.chutes.ai/v1")
VISION_MODEL = os.getenv("VISION_MODEL", "google/gemma-4-31B-turbo-TEE")
REASONING_MODEL = os.getenv("REASONING_MODEL", "google/gemma-4-31B-turbo-TEE")

# Bank fee config — percentage charged on inbound foreign currency conversions
BANK_FEES = {
    "Maybank": 0.005,
    "CIMB": 0.006,
    "Public Bank": 0.005,
    "HSBC": 0.004,
    "Wise": 0.004,
    "default": 0.005,
}

# Tolerance for amount matching after FX + fees (relative)
MATCH_TOLERANCE = 0.02  # 2%
