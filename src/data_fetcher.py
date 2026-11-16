from pathlib import Path

import pandas as pd

from config import DATA_RAW_DIR


DATA_URL = (
    "https://ourworldindata.org/grapher/"
    "annual-co2-emissions-per-country.csv"
    "?v=1&csvType=full&useColumnShortNames=false"
)

OUTPUT_FILE = DATA_RAW_DIR / "external_co2_data.csv"


def fetch_co2_data() -> Path:
    """Download the public CO2 emissions dataset."""

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    print("Downloading public CO2 emissions dataset...")

    dataframe = pd.read_csv(
        DATA_URL,
        storage_options={
            "User-Agent": "Our World In Data data fetch/1.0"
        },
    )

    dataframe.to_csv(OUTPUT_FILE, index=False)

    print(f"Dataset saved to: {OUTPUT_FILE}")
    print(f"Rows: {len(dataframe)}")
    print(f"Columns: {len(dataframe.columns)}")

    return OUTPUT_FILE


if __name__ == "__main__":
    fetch_co2_data()