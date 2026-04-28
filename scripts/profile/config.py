from pathlib import Path

PROJECT_ROOT = Path.home() / "MaxPain_Project"
PARQUET_ROOT = PROJECT_ROOT / "data" / "orats" / "parquet"
PROFILE_ROOT = PROJECT_ROOT / "data" / "profile"
DAILY_DIR = PROFILE_ROOT / "daily_summary"
PROFILE_PATH = PROFILE_ROOT / "profile_v1.parquet"
LOG_PATH = PROJECT_ROOT / "logs" / "profile_build.log"

MIN_STRIKES_FOR_SKEW = 20
SKEW_DELTA = 0.10
