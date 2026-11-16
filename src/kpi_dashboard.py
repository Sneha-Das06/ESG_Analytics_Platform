"""Chart-ready views of the already authorized ESG workbook frames."""

import pandas as pd

from src.data_loader import EXPECTED_SHEETS


def _select(frame, columns):
    selected = [column for column in columns if column in frame.columns]
    return frame.loc[:, selected].copy()


def style_kpi_figure(figure):
    """Apply the platform's high-contrast styling to KPI Analytics charts only."""
    navy = "#0b1f33"
    secondary = "#243f52"
    teal = "#16856f"
    grid = "#d5e0e5"
    axis = {
        "title": {"font": {"family": "Arial, sans-serif", "size": 14, "color": navy, "weight": 600}},
        "tickfont": {"family": "Arial, sans-serif", "size": 12, "color": secondary, "weight": 600},
        "automargin": True,
        "linecolor": "#8295a1",
        "linewidth": 1,
        "zerolinecolor": "#a8b8c1",
        "zerolinewidth": 1,
    }
    figure.update_layout(
        paper_bgcolor="#ffffff",
        plot_bgcolor="#ffffff",
        font={"family": "Arial, sans-serif", "size": 13, "color": navy, "weight": 500},
        title={"font": {"family": "Arial, sans-serif", "size": 18, "color": navy, "weight": 700}, "x": 0.02, "xanchor": "left"},
        legend={
            "font": {"family": "Arial, sans-serif", "size": 12, "color": secondary, "weight": 600},
            "title": {"font": {"family": "Arial, sans-serif", "size": 12, "color": navy, "weight": 700}},
            "bgcolor": "rgba(255,255,255,0.96)",
            "bordercolor": "#d5e0e5",
            "borderwidth": 1,
        },
        hoverlabel={
            "bgcolor": navy,
            "bordercolor": teal,
            "font": {"family": "Arial, sans-serif", "size": 13, "color": "#ffffff", "weight": 600},
        },
        xaxis={**axis, "showgrid": False, "gridcolor": grid},
        yaxis={**axis, "showgrid": True, "gridcolor": grid, "gridwidth": 1},
        margin={"l": 65, "r": 25, "t": 65, "b": 60},
    )
    figure.update_traces(textfont={"family": "Arial, sans-serif", "size": 12, "color": navy, "weight": 600})
    figure.update_annotations(font={"family": "Arial, sans-serif", "size": 12, "color": navy, "weight": 600})
    return figure


def build_kpi_chart_data(company_data):
    """Return only fields needed by KPI charts; callers must pass scoped data."""
    energy, water, waste, hr, safety, suppliers, procurement = (
        company_data.get(sheet) for sheet in EXPECTED_SHEETS
    )
    if "Department" in hr.columns and "Employee_ID" in hr.columns:
        employees_by_department = (
            hr.groupby("Department", as_index=False)["Employee_ID"]
            .nunique()
            .rename(columns={"Employee_ID": "Employees"})
        )
    else:
        employees_by_department = pd.DataFrame(columns=["Department", "Employees"])
    return {
        "Energy": _select(energy, ("Reporting_Period", "Electricity_kWh", "Solar_kWh", "Renewable_Energy_kWh", "Diesel_Litres", "Carbon_Emissions_tCO2e")),
        "Water": _select(water, ("Reporting_Period", "Water_Consumed_m3", "Water_Used_m3", "Recycled_Water_m3")),
        "Waste": _select(waste, ("Reporting_Period", "Total_Waste_Tonnes", "Recycled_Waste_Tonnes", "Hazardous_Waste_Tonnes")),
        "HR": _select(hr, ("Reporting_Period", "Department", "Training_Hours")),
        "HR_Employees_By_Department": employees_by_department,
        "Safety": _select(safety, ("Reporting_Period", "Recordable_Incidents", "Affected_Employees", "Lost_Days", "Hours_Worked")),
        "Supplier": _select(suppliers, ("Supplier_ID", "ESG_Score", "ISO14001_Certified")),
        "Procurement": _select(procurement, ("Reporting_Period", "Purchase_Amount", "Sustainable_Material_Quantity", "Sustainable_Material_Percent", "Sustainable_Material_%")),
    }
