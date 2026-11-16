import pandas as pd


def _numeric_series(df, column):
    """Return a numeric series for a dataframe column."""

    if column not in df.columns:
        return pd.Series(dtype="float64")

    return pd.to_numeric(
        df[column],
        errors="coerce",
    )


def _number_sum(df, column):
    """Return the sum of valid numeric values."""

    values = _numeric_series(df, column)

    if values.empty:
        return 0.0

    return float(values.sum())


def _number_mean(df, column):
    """Return the mean of valid numeric values."""

    values = _numeric_series(df, column)

    if values.empty:
        return 0.0

    return float(values.mean())


def _first_available(df, columns):
    """Return the first available column name."""

    for column in columns:
        if column in df.columns:
            return column

    return None


def _calculate_percentage(numerator, denominator):
    """Calculate a percentage safely."""

    if denominator <= 0:
        return 0.0

    return float(
        numerator / denominator * 100
    )


def _calculate_employee_count(hr):
    """Calculate unique employee count."""

    if "Employee_ID" not in hr.columns:
        return 0

    return int(
        hr["Employee_ID"]
        .dropna()
        .nunique()
    )


def _calculate_supplier_count(supplier):
    """Calculate unique supplier count."""

    if "Supplier_ID" not in supplier.columns:
        return 0

    return int(
        supplier["Supplier_ID"]
        .dropna()
        .nunique()
    )


def _calculate_iso_certification_percent(supplier):
    """Calculate percentage of ISO 14001 certified suppliers."""

    column = "ISO14001_Certified"

    if column not in supplier.columns:
        return 0.0

    values = (
        supplier[column]
        .astype("string")
        .str.strip()
        .str.lower()
    )

    certified_values = {
        "yes",
        "true",
        "1",
        "certified",
    }

    valid_values = values.dropna()

    if valid_values.empty:
        return 0.0

    certified_count = valid_values.isin(
        certified_values
    ).sum()

    return _calculate_percentage(
        certified_count,
        len(valid_values),
    )


def calculate_kpis(company_data):
    """Calculate ESG KPIs from one company's ESG datasets."""

    energy = company_data.get(
        "⚡ Energy_Data",
        pd.DataFrame(),
    )

    water = company_data.get(
        "💧 Water_Data",
        pd.DataFrame(),
    )

    waste = company_data.get(
        "♻️  Waste_Data",
        pd.DataFrame(),
    )

    hr = company_data.get(
        "👥  HR_Data",
        pd.DataFrame(),
    )

    safety = company_data.get(
        "🦺 Safety_Data",
        pd.DataFrame(),
    )

    supplier = company_data.get(
        "🏭 Supplier_Data",
        pd.DataFrame(),
    )

    procurement = company_data.get(
        "🛒 Procurement_Data",
        pd.DataFrame(),
    )

    # --------------------------------------------------
    # ⚡ Environmental — Energy
    # --------------------------------------------------

    electricity = _number_sum(
        energy,
        "Electricity_kWh",
    )

    diesel = _number_sum(
        energy,
        "Diesel_Litres",
    )

    solar = _number_sum(
        energy,
        "Solar_kWh",
    )

    renewable = _number_sum(
        energy,
        "Renewable_Energy_kWh",
    )

    total_energy_column = _first_available(
        energy,
        ["Total_Energy_kWh"],
    )

    if total_energy_column:
        total_energy = _number_sum(
            energy,
            total_energy_column,
        )
    else:
        total_energy = (
            electricity
            + diesel
            + solar
            + renewable
        )

    renewable_energy_percent = (
        _calculate_percentage(
            renewable,
            total_energy,
        )
    )

    # --------------------------------------------------
    # 💧 Environmental — Water
    # --------------------------------------------------

    water_used_column = _first_available(
        water,
        [
            "Water_Consumed_m3",
            "Water_Used_m3",
        ],
    )

    if water_used_column:
        water_used = _number_sum(
            water,
            water_used_column,
        )
    else:
        water_used = 0.0

    recycled_water = _number_sum(
        water,
        "Recycled_Water_m3",
    )

    water_recycling_percent = (
        _calculate_percentage(
            recycled_water,
            water_used,
        )
    )

    # --------------------------------------------------
    # ♻️ Environmental — Waste
    # --------------------------------------------------

    total_waste = _number_sum(
        waste,
        "Total_Waste_Tonnes",
    )

    recycled_waste = _number_sum(
        waste,
        "Recycled_Waste_Tonnes",
    )

    hazardous_waste = _number_sum(
        waste,
        "Hazardous_Waste_Tonnes",
    )

    waste_recycling_percent = (
        _calculate_percentage(
            recycled_waste,
            total_waste,
        )
    )

    # --------------------------------------------------
    # 👥 Social — Human Resources
    # --------------------------------------------------

    employee_count = _calculate_employee_count(
        hr
    )

    total_training_hours = _number_sum(
        hr,
        "Training_Hours",
    )

    average_training_hours = _number_mean(
        hr,
        "Training_Hours",
    )

    # --------------------------------------------------
    # 🦺 Social — Safety
    # --------------------------------------------------

    incident_column = _first_available(safety, ["Recordable_Incidents", "Accident_Count"])
    accident_count = _number_sum(safety, incident_column) if incident_column else len(safety)

    lost_days = _number_sum(
        safety,
        "Lost_Days",
    )

    affected_employees = _number_sum(
        safety,
        "Affected_Employees",
    )

    # --------------------------------------------------
    # 🏭 Governance — Suppliers
    # --------------------------------------------------

    supplier_count = _calculate_supplier_count(
        supplier
    )

    average_supplier_esg = _number_mean(
        supplier,
        "ESG_Score",
    )

    iso14001_percent = (
        _calculate_iso_certification_percent(
            supplier
        )
    )

    # --------------------------------------------------
    # 🛒 Governance — Procurement
    # --------------------------------------------------

    purchase_amount = _number_sum(
        procurement,
        "Purchase_Amount",
    )

    sustainable_percent_column = _first_available(
        procurement,
        [
            "Sustainable_Material_%",
            "Sustainable_Material_Percent",
            "Average_Sustainable_Material_%",
            "Average_Sustainable_Material_Percent",
        ],
    )
    sustainable_material_percent = (
        _number_mean(procurement, sustainable_percent_column)
        if sustainable_percent_column else 0.0
    )

    sustainable_material_quantity = _number_sum(
        procurement,
        "Sustainable_Material_Quantity",
    )

    # --------------------------------------------------
    # Final KPI structure
    # --------------------------------------------------

    return {
        "Energy": {
            "Electricity_kWh": electricity,
            "Diesel_Litres": diesel,
            "Solar_kWh": solar,
            "Renewable_Energy_kWh": renewable,
            "Total_Energy_kWh": total_energy,
            "Renewable_Energy_%": renewable_energy_percent,
        },

        "Water": {
            "Water_Used_m3": water_used,
            "Recycled_Water_m3": recycled_water,
            "Water_Recycling_%": water_recycling_percent,
        },

        "Waste": {
            "Total_Waste_Tonnes": total_waste,
            "Recycled_Waste_Tonnes": recycled_waste,
            "Hazardous_Waste_Tonnes": hazardous_waste,
            "Waste_Recycling_%": waste_recycling_percent,
        },

        "HR": {
            "Employee_Count": employee_count,
            "Total_Training_Hours": total_training_hours,
            "Average_Training_Hours": average_training_hours,
        },

        "Safety": {
            "Accident_Count": accident_count,
            "Lost_Days": lost_days,
            "Affected_Employees": affected_employees,
        },

        "Supplier": {
            "Supplier_Count": supplier_count,
            "Average_ESG_Score": average_supplier_esg,
            "ISO14001_Certified_%": iso14001_percent,
        },

        "Procurement": {
            "Purchase_Amount": purchase_amount,
            "Average_Sustainable_Material_%":
                sustainable_material_percent,
            "Sustainable_Material_Quantity":
                sustainable_material_quantity,
        },
    }