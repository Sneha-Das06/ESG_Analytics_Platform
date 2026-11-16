import pandas as pd


def clean_column_names(df):
    """Standardize dataframe column names."""

    df = df.copy()

    df.columns = (
        df.columns
        .astype(str)
        .str.strip()
        .str.replace(" ", "_", regex=False)
        .str.replace("%", "Percent", regex=False)
    )

    return df


def remove_invalid_rows(df):
    """Compatibility helper: keep source rows until an explicit cleaning is approved.

    Duplicate and empty rows are reported by ``src.data_quality`` and may only be
    removed from a new cleaned copy after user confirmation.
    """
    return df.copy(deep=True)


def clean_text_columns(df):
    """Clean whitespace from text columns."""

    df = df.copy()

    text_columns = df.select_dtypes(
        include="object"
    ).columns

    for column in text_columns:
        df[column] = (
            df[column]
            .astype("string")
            .str.strip()
        )

        # Convert empty strings to missing values
        df[column] = df[column].replace(
            "",
            pd.NA,
        )

    return df


def convert_numeric_columns(df):
    """Convert columns containing numeric data to numeric types."""

    df = df.copy()

    for column in df.columns:

        if df[column].dtype == "object":
            converted = pd.to_numeric(
                df[column],
                errors="coerce",
            )

            original_non_null = (
                df[column].notna().sum()
            )

            converted_non_null = (
                converted.notna().sum()
            )

            if (
                original_non_null > 0
                and converted_non_null
                == original_non_null
            ):
                df[column] = converted

    return df


def clean_percentage_columns(df):
    """Validate and clean percentage values."""

    df = df.copy()

    percentage_columns = [
        column
        for column in df.columns
        if "Percent" in column
        or "%" in column
    ]

    for column in percentage_columns:

        original = df[column].copy()
        candidate = pd.to_numeric(
            original.astype("string").str.strip().str.replace(r"\s*%\s*$", "", regex=True),
            errors="coerce",
        )
        # Convert only a fully parseable percentage column. Invalid values remain
        # available for validation and are never silently replaced with nulls.
        if candidate[original.notna()].notna().all():
            df[column] = candidate

    return df


def clean_dataframe(df):
    """Clean and standardize one ESG dataframe."""

    df = df.copy()

    df = clean_column_names(df)

    df = remove_invalid_rows(df)

    df = clean_text_columns(df)

    df = convert_numeric_columns(df)

    df = clean_percentage_columns(df)

    return df


def preprocess_company_data(company_data):
    """Preprocess all ESG sheets for one company."""

    processed_data = {}

    for sheet_name, dataframe in company_data.items():

        processed_data[sheet_name] = clean_dataframe(
            dataframe
        )

    return processed_data
