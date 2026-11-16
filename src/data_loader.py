from pathlib import Path

import pandas as pd


EXPECTED_SHEETS = [
    "⚡ Energy_Data",
    "💧 Water_Data",
    "♻️  Waste_Data",
    "👥  HR_Data",
    "🦺 Safety_Data",
    "🏭 Supplier_Data",
    "🛒 Procurement_Data",
]


def load_company_data(file_path):
    """Load all ESG sheets from a company Excel workbook."""

    file_path = Path(file_path)

    if not file_path.exists():
        raise FileNotFoundError(
            f"Company data file not found: {file_path}"
        )

    excel_data = pd.read_excel(
        file_path,
        sheet_name=None
    )

    missing_sheets = [
        sheet
        for sheet in EXPECTED_SHEETS
        if sheet not in excel_data
    ]

    if missing_sheets:
        raise ValueError(
            f"Missing ESG sheets in {file_path.name}: "
            f"{missing_sheets}"
        )

    company_data = {
        sheet: excel_data[sheet]
        for sheet in EXPECTED_SHEETS
    }

    return company_data


def load_external_co2_data(file_path):
    """Load the public external CO2 dataset from a CSV file."""

    file_path = Path(file_path)

    if not file_path.exists():
        raise FileNotFoundError(
            f"External CO2 dataset not found: {file_path}"
        )

    try:
        dataframe = pd.read_csv(file_path)
    except Exception as error:
        raise ValueError(
            f"Unable to read external CO2 dataset: {error}"
        ) from error

    if dataframe.empty:
        raise ValueError(
            "External CO2 dataset is empty."
        )

    return dataframe