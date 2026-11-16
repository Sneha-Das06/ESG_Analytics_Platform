def analyze_company(
    company_name,
    kpis,
    external_co2_data=None,
):
    """Generate analytical insights from company ESG KPIs."""

    insights = []

    energy = kpis.get("Energy", {})
    water = kpis.get("Water", {})
    waste = kpis.get("Waste", {})
    supplier = kpis.get("Supplier", {})
    procurement = kpis.get("Procurement", {})

    renewable_percent = energy.get(
        "Renewable_Energy_%",
        0,
    )

    water_recycling_percent = water.get(
        "Water_Recycling_%",
        0,
    )

    waste_recycling_percent = waste.get(
        "Waste_Recycling_%",
        0,
    )

    supplier_esg = supplier.get(
        "Average_ESG_Score",
        0,
    )

    sustainable_material_percent = procurement.get(
        "Average_Sustainable_Material_%",
        0,
    )

    # Environmental analysis
    if renewable_percent >= 30:
        insights.append(
            "Renewable energy contribution is relatively strong."
        )
    else:
        insights.append(
            "Renewable energy contribution can be improved."
        )

    if water_recycling_percent >= 50:
        insights.append(
            "Water recycling performance is relatively strong."
        )
    else:
        insights.append(
            "Water recycling performance can be improved."
        )

    if waste_recycling_percent >= 50:
        insights.append(
            "Waste recycling performance is relatively strong."
        )
    else:
        insights.append(
            "Waste recycling performance can be improved."
        )

    # Governance analysis
    if supplier_esg >= 70:
        insights.append(
            "Supplier ESG performance is relatively strong."
        )
    else:
        insights.append(
            "Supplier ESG performance should be monitored."
        )

    if sustainable_material_percent >= 50:
        insights.append(
            "Sustainable material procurement is relatively strong."
        )
    else:
        insights.append(
            "Sustainable material procurement can be improved."
        )

    # External CO2 dataset analysis
    external_co2_summary = {}

    if external_co2_data is not None:

        required_columns = {
            "Entity",
            "Year",
            "Annual CO₂ emissions",
        }

        if required_columns.issubset(
            external_co2_data.columns
        ):

            latest_year = external_co2_data["Year"].max()

            latest_data = external_co2_data[
                external_co2_data["Year"] == latest_year
            ]

            total_global_co2 = (
                latest_data["Annual CO₂ emissions"]
                .sum()
            )

            external_co2_summary = {
                "latest_year": int(latest_year),
                "entities_available": int(
                    latest_data["Entity"].nunique()
                ),
                "total_reported_co2": float(
                    total_global_co2
                ),
            }

            insights.append(
                "External public CO2 data was successfully "
                "loaded and included in the ESG analysis."
            )

    return {
        "company": company_name,
        "insights": insights,
        "external_co2_summary": external_co2_summary,
    }