import os
from pathlib import Path

from dotenv import load_dotenv


# ==========================================
# PROJECT ROOT
# ==========================================

BASE_DIR = Path(__file__).resolve().parent


# ==========================================
# LOAD .ENV
# ==========================================

ENV_FILE = BASE_DIR / ".env"

load_dotenv(ENV_FILE)


# ==========================================
# APPLICATION SETTINGS
# ==========================================

PROJECT_NAME = os.getenv(
    "PROJECT_NAME",
    "ESG Analytics Platform"
)

DASHBOARD_NAME = os.getenv(
    "DASHBOARD_NAME",
    "ESG Analytics Dashboard"
)


# ==========================================
# FEATURE SETTINGS
# ==========================================

ENABLE_EXTERNAL_CO2 = (
    os.getenv(
        "ENABLE_EXTERNAL_CO2",
        "true"
    ).lower()
    == "true"
)

ENABLE_VISUALIZATIONS = (
    os.getenv(
        "ENABLE_VISUALIZATIONS",
        "true"
    ).lower()
    == "true"
)

ENABLE_REPORTS = (
    os.getenv(
        "ENABLE_REPORTS",
        "true"
    ).lower()
    == "true"
)


# ==========================================
# LOGGING
# ==========================================

LOG_LEVEL = os.getenv(
    "LOG_LEVEL",
    "INFO"
)


# ==========================================
# DIRECTORIES
# ==========================================

DATA_DIR = BASE_DIR / "data"

DATA_RAW_DIR = (
    DATA_DIR / "raw"
)

DATA_PROCESSED_DIR = (
    DATA_DIR / "processed"
)

OUTPUT_DIR = (
    BASE_DIR
    / os.getenv(
        "OUTPUT_DIR",
        "outputs"
    )
)

REPORT_DIR = (
    BASE_DIR
    / os.getenv(
        "REPORT_DIR",
        "outputs"
    )
)

VISUALIZATION_DIR = (
    BASE_DIR
    / os.getenv(
        "VISUALIZATION_DIR",
        "outputs/visualizations"
    )
)

CONFIG_DIR = (
    BASE_DIR / "config"
)


# ==========================================
# FILE PATHS
# ==========================================

COMPANY_CONFIG_FILE = (
    BASE_DIR
    / os.getenv(
        "COMPANY_CONFIG_FILE",
        "config/companies.json"
    )
)

CO2_DATA_FILE = (
    BASE_DIR
    / os.getenv(
        "EXTERNAL_CO2_FILE",
        "data/raw/external_co2_data.csv"
    )
)

# ==========================================
# CREATE REQUIRED DIRECTORIES
# ==========================================

DATA_PROCESSED_DIR.mkdir(
    parents=True,
    exist_ok=True
)

OUTPUT_DIR.mkdir(
    parents=True,
    exist_ok=True
)

REPORT_DIR.mkdir(
    parents=True,
    exist_ok=True
)

VISUALIZATION_DIR.mkdir(
    parents=True,
    exist_ok=True
)
