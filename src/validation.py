import pandas as pd


PERCENTAGE_COLUMNS = {
    "Renewable_Energy_%",
    "Water_Recycling_%",
    "Waste_Recycling_%",
    "Average_Sustainable_Material_%",
    "ISO14001_Certified_%",
}


def validate_dataframe(df, sheet_name):
    """Validate one ESG dataset."""

    issues = []

    # 1. Empty dataset
    if df.empty:
        issues.append(
            f"{sheet_name}: dataset is empty."
        )
        return issues

    # 2. Duplicate column names
    if df.columns.duplicated().any():
        duplicate_columns = (
            df.columns[df.columns.duplicated()]
            .tolist()
        )

        issues.append(
            f"{sheet_name}: duplicate column names found: "
            f"{duplicate_columns}"
        )

    # 3. Completely empty columns
    empty_columns = (
        df.columns[df.isnull().all()]
        .tolist()
    )

    if empty_columns:
        issues.append(
            f"{sheet_name}: completely empty columns: "
            f"{empty_columns}"
        )

    # 4. Duplicate rows
    duplicate_rows = df.duplicated().sum()

    if duplicate_rows > 0:
        issues.append(
            f"{sheet_name}: {duplicate_rows} duplicate "
            f"rows found."
        )

    # 5. Missing values
    missing_values = df.isnull().sum()

    columns_with_missing = (
        missing_values[missing_values > 0]
        .to_dict()
    )

    if columns_with_missing:
        issues.append(
            f"{sheet_name}: missing values found: "
            f"{columns_with_missing}"
        )

    # 6. Negative numeric values
    numeric_columns = df.select_dtypes(
        include="number"
    ).columns

    for column in numeric_columns:

        negative_count = (
            df[column] < 0
        ).sum()

        if negative_count > 0:
            issues.append(
                f"{sheet_name}: column '{column}' "
                f"contains {negative_count} negative "
                f"value(s)."
            )

    # 7. Percentage validation
    for column in PERCENTAGE_COLUMNS:

        if column not in df.columns:
            continue

        numeric_values = pd.to_numeric(
            df[column],
            errors="coerce"
        )

        invalid_percentage = (
            (numeric_values < 0)
            | (numeric_values > 100)
        ).sum()

        if invalid_percentage > 0:
            issues.append(
                f"{sheet_name}: column '{column}' "
                f"contains {invalid_percentage} "
                f"value(s) outside 0-100%."
            )

    return issues


def validate_company_data(company_data):
    """Validate all datasets for one company."""

    validation_results = {}

    for sheet_name, dataframe in company_data.items():

        validation_results[sheet_name] = (
            validate_dataframe(
                dataframe,
                sheet_name
            )
        )

    return validation_results