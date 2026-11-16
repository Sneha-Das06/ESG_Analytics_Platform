from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.patches import FancyBboxPatch


# ============================================================
# Dashboard Theme
# ============================================================

BACKGROUND_COLOR = "#F4F7FB"
PANEL_COLOR = "#FFFFFF"
TEXT_COLOR = "#172033"
MUTED_COLOR = "#667085"
GRID_COLOR = "#E4E7EC"

ENVIRONMENTAL_COLOR = "#2E7D32"
SOCIAL_COLOR = "#1565C0"
GOVERNANCE_COLOR = "#6A1B9A"
WARNING_COLOR = "#E67E22"
POSITIVE_COLOR = "#1B8A5A"


# ============================================================
# Data Preparation
# ============================================================

def prepare_kpi_dataframe(all_results):
    """Convert company KPI results into a comparison DataFrame."""

    records = []

    for result in all_results:
        company = result.get("company", "")
        kpis = result.get("kpis", {})

        record = {"Company": company}

        for category, metrics in kpis.items():

            if not isinstance(metrics, dict):
                continue

            for metric_name, value in metrics.items():

                numeric_value = pd.to_numeric(
                    value,
                    errors="coerce",
                )

                if pd.notna(numeric_value):
                    record[
                        f"{category}_{metric_name}"
                    ] = float(numeric_value)

        records.append(record)

    return pd.DataFrame(records)


# ============================================================
# Formatting Helpers
# ============================================================

def format_number(value):
    """Format large numeric values for readable charts."""

    if value is None or pd.isna(value):
        return "N/A"

    value = float(value)

    if abs(value) >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f}B"

    if abs(value) >= 1_000_000:
        return f"{value / 1_000_000:.2f}M"

    if abs(value) >= 1_000:
        return f"{value / 1_000:.2f}K"

    return f"{value:,.2f}"


def format_percentage(value):
    """Format percentage values."""

    if value is None or pd.isna(value):
        return "N/A"

    return f"{float(value):.1f}%"


def safe_numeric_series(dataframe, column):
    """Return a numeric series for a KPI column."""

    if column not in dataframe.columns:
        return pd.Series(dtype=float)

    return pd.to_numeric(
        dataframe[column],
        errors="coerce",
    )


# ============================================================
# Professional Bar Chart
# ============================================================

def save_bar_chart(
    dataframe,
    metric,
    title,
    output_path,
    y_label=None,
    section_color=ENVIRONMENTAL_COLOR,
):
    """Create a professional company comparison bar chart."""

    if metric not in dataframe.columns:
        return False

    chart_data = dataframe[
        ["Company", metric]
    ].copy()

    chart_data[metric] = pd.to_numeric(
        chart_data[metric],
        errors="coerce",
    )

    chart_data = chart_data.dropna()

    if chart_data.empty:
        return False

    figure, axis = plt.subplots(
        figsize=(12, 7),
    )

    figure.patch.set_facecolor(
        BACKGROUND_COLOR
    )

    axis.set_facecolor(
        PANEL_COLOR
    )

    bars = axis.bar(
        chart_data["Company"],
        chart_data[metric],
        width=0.62,
        color=section_color,
        edgecolor="white",
        linewidth=1.2,
    )

    axis.set_title(
        title,
        fontsize=19,
        fontweight="bold",
        color=TEXT_COLOR,
        loc="left",
        pad=20,
    )

    axis.set_ylabel(
        y_label or metric,
        fontsize=11,
        color=MUTED_COLOR,
    )

    axis.tick_params(
        axis="x",
        rotation=15,
        labelsize=10,
        colors=TEXT_COLOR,
    )

    axis.tick_params(
        axis="y",
        labelsize=9,
        colors=MUTED_COLOR,
    )

    axis.grid(
        axis="y",
        linestyle="--",
        linewidth=0.8,
        alpha=0.35,
        color=GRID_COLOR,
    )

    axis.set_axisbelow(True)

    for spine in axis.spines.values():
        spine.set_visible(False)

    max_value = chart_data[metric].max()

    if max_value > 0:
        axis.set_ylim(
            0,
            max_value * 1.18,
        )

    for bar, value in zip(
        bars,
        chart_data[metric],
    ):
        axis.text(
            bar.get_x()
            + bar.get_width() / 2,
            bar.get_height(),
            format_number(value),
            ha="center",
            va="bottom",
            fontsize=9,
            fontweight="bold",
            color=TEXT_COLOR,
        )

    figure.tight_layout()

    figure.savefig(
        output_path,
        dpi=300,
        bbox_inches="tight",
        facecolor=figure.get_facecolor(),
    )

    plt.close(figure)

    return True


# ============================================================
# Category Chart Generator
# ============================================================

def generate_category_charts(
    dataframe,
    output_dir,
    charts,
    section_color,
):
    """Generate multiple professional KPI charts."""

    for metric, chart_information in charts.items():

        title, y_label = chart_information

        output_path = (
            output_dir
            / f"{metric.lower()}_comparison.png"
        )

        save_bar_chart(
            dataframe,
            metric,
            title,
            output_path,
            y_label,
            section_color,
        )


# ============================================================
# Environmental Charts
# ============================================================

def generate_environmental_charts(
    dataframe,
    output_dir,
):
    """Generate environmental KPI comparison charts."""

    charts = {
        "Energy_Total_Energy_kWh": (
            "Total Energy Consumption",
            "Energy (kWh)",
        ),
        "Energy_Renewable_Energy_%": (
            "Renewable Energy Contribution",
            "Renewable Energy (%)",
        ),
        "Water_Water_Used_m3": (
            "Water Consumption",
            "Water Used (m³)",
        ),
        "Water_Water_Recycling_%": (
            "Water Recycling Performance",
            "Water Recycling (%)",
        ),
        "Waste_Total_Waste_Tonnes": (
            "Total Waste Generated",
            "Waste (Tonnes)",
        ),
        "Waste_Waste_Recycling_%": (
            "Waste Recycling Performance",
            "Waste Recycling (%)",
        ),
    }

    generate_category_charts(
        dataframe,
        output_dir,
        charts,
        ENVIRONMENTAL_COLOR,
    )


# ============================================================
# Social Charts
# ============================================================

def generate_social_charts(
    dataframe,
    output_dir,
):
    """Generate social KPI comparison charts."""

    charts = {
        "HR_Employee_Count": (
            "Employee Count",
            "Employees",
        ),
        "HR_Total_Training_Hours": (
            "Total Employee Training Hours",
            "Training Hours",
        ),
        "HR_Average_Training_Hours": (
            "Average Training Hours per Employee",
            "Hours",
        ),
        "Safety_Accident_Count": (
            "Workplace Safety Incidents",
            "Incidents",
        ),
        "Safety_Lost_Days": (
            "Safety-Related Lost Days",
            "Lost Days",
        ),
    }

    generate_category_charts(
        dataframe,
        output_dir,
        charts,
        SOCIAL_COLOR,
    )


# ============================================================
# Governance Charts
# ============================================================

def generate_governance_charts(
    dataframe,
    output_dir,
):
    """Generate governance KPI comparison charts."""

    charts = {
        "Supplier_Average_ESG_Score": (
            "Average Supplier ESG Score",
            "ESG Score",
        ),
        "Supplier_ISO14001_Certified_%": (
            "ISO 14001 Certified Suppliers",
            "Certified Suppliers (%)",
        ),
        "Procurement_Average_Sustainable_Material_%": (
            "Sustainable Material Procurement",
            "Sustainable Material (%)",
        ),
    }

    generate_category_charts(
        dataframe,
        output_dir,
        charts,
        GOVERNANCE_COLOR,
    )


# ============================================================
# Best Company Helper
# ============================================================

def find_best_company(
    dataframe,
    metric,
    higher_is_better=True,
):
    """Find the best-performing company for a KPI."""

    if metric not in dataframe.columns:
        return None, None

    data = dataframe[
        ["Company", metric]
    ].copy()

    data[metric] = pd.to_numeric(
        data[metric],
        errors="coerce",
    )

    data = data.dropna()

    if data.empty:
        return None, None

    if higher_is_better:
        row = data.loc[
            data[metric].idxmax()
        ]
    else:
        row = data.loc[
            data[metric].idxmin()
        ]

    return (
        row["Company"],
        float(row[metric]),
    )


# ============================================================
# KPI Card
# ============================================================

def draw_kpi_card(
    figure,
    x,
    y,
    width,
    height,
    title,
    value,
    subtitle="",
    accent_color=ENVIRONMENTAL_COLOR,
):
    """Draw a professional KPI card."""

    axis = figure.add_axes(
        [x, y, width, height]
    )

    axis.set_xlim(0, 1)
    axis.set_ylim(0, 1)
    axis.axis("off")

    card = FancyBboxPatch(
        (0, 0),
        1,
        1,
        boxstyle="round,pad=0.012,rounding_size=0.035",
        facecolor=PANEL_COLOR,
        edgecolor="#E5E7EB",
        linewidth=1.2,
    )

    axis.add_patch(card)

    axis.add_patch(
        FancyBboxPatch(
            (0, 0),
            0.018,
            1,
            boxstyle="round,pad=0,rounding_size=0.01",
            facecolor=accent_color,
            edgecolor=accent_color,
        )
    )

    axis.text(
        0.08,
        0.72,
        title,
        fontsize=9,
        color=MUTED_COLOR,
        fontweight="bold",
    )

    axis.text(
        0.08,
        0.43,
        value,
        fontsize=17,
        color=TEXT_COLOR,
        fontweight="bold",
    )

    if subtitle:
        axis.text(
            0.08,
            0.16,
            subtitle,
            fontsize=8,
            color=MUTED_COLOR,
        )


# ============================================================
# Dashboard Line Chart
# ============================================================

def draw_percentage_chart(
    figure,
    dataframe,
    metrics,
    labels,
    position,
    title,
):
    """Draw a clean multi-series percentage chart."""

    axis = figure.add_axes(position)

    axis.set_facecolor(PANEL_COLOR)

    for metric, label in zip(
        metrics,
        labels,
    ):
        values = safe_numeric_series(
            dataframe,
            metric,
        )

        if values.empty:
            continue

        axis.plot(
            dataframe["Company"],
            values,
            marker="o",
            linewidth=2.5,
            markersize=6,
            label=label,
        )

    axis.set_title(
        title,
        fontsize=13,
        fontweight="bold",
        color=TEXT_COLOR,
        loc="left",
        pad=12,
    )

    axis.tick_params(
        axis="x",
        rotation=15,
        labelsize=8,
    )

    axis.tick_params(
        axis="y",
        labelsize=8,
    )

    axis.grid(
        axis="y",
        linestyle="--",
        alpha=0.3,
    )

    for spine in axis.spines.values():
        spine.set_visible(False)

    axis.legend(
        fontsize=7,
        frameon=False,
        loc="upper left",
    )


# ============================================================
# Dashboard Bar Chart
# ============================================================

def draw_comparison_chart(
    figure,
    dataframe,
    metrics,
    labels,
    position,
    title,
):
    """Draw a grouped company comparison chart."""

    axis = figure.add_axes(position)

    axis.set_facecolor(PANEL_COLOR)

    companies = dataframe["Company"].tolist()

    positions = range(len(companies))

    number_of_metrics = len(metrics)

    width = (
        0.72 / number_of_metrics
        if number_of_metrics
        else 0.35
    )

    for index, (
        metric,
        label,
    ) in enumerate(
        zip(metrics, labels)
    ):

        values = safe_numeric_series(
            dataframe,
            metric,
        )

        if values.empty:
            continue

        offsets = [
            position
            - 0.36
            + width / 2
            + index * width
            for position in positions
        ]

        axis.bar(
            offsets,
            values,
            width=width,
            label=label,
        )

    axis.set_title(
        title,
        fontsize=13,
        fontweight="bold",
        color=TEXT_COLOR,
        loc="left",
        pad=12,
    )

    axis.set_xticks(
        list(positions)
    )

    axis.set_xticklabels(
        companies,
        rotation=15,
        fontsize=8,
    )

    axis.tick_params(
        axis="y",
        labelsize=8,
    )

    axis.grid(
        axis="y",
        linestyle="--",
        alpha=0.3,
    )

    for spine in axis.spines.values():
        spine.set_visible(False)

    axis.legend(
        fontsize=7,
        frameon=False,
    )


# ============================================================
# Management Dashboard
# ============================================================

def generate_management_dashboard(
    dataframe,
    output_dir,
):
    """Generate the executive ESG management dashboard."""

    if dataframe.empty:
        return False

    output_file = (
        output_dir
        / "esg_management_dashboard.png"
    )

    figure = plt.figure(
        figsize=(18, 12),
        facecolor=BACKGROUND_COLOR,
    )

    # --------------------------------------------------------
    # Header
    # --------------------------------------------------------

    header = figure.add_axes(
        [0, 0.91, 1, 0.09]
    )

    header.set_facecolor(
        TEXT_COLOR
    )

    header.set_xlim(0, 1)
    header.set_ylim(0, 1)
    header.axis("off")

    header.text(
        0.035,
        0.62,
        "ESG MANAGEMENT DASHBOARD",
        fontsize=23,
        fontweight="bold",
        color="white",
    )

    header.text(
        0.035,
        0.25,
        "Environmental • Social • Governance Performance Overview",
        fontsize=9,
        color="#D0D5DD",
    )

    # --------------------------------------------------------
    # Best-performing companies
    # --------------------------------------------------------

    renewable_company, renewable_value = find_best_company(
        dataframe,
        "Energy_Renewable_Energy_%",
    )

    water_company, water_value = find_best_company(
        dataframe,
        "Water_Water_Recycling_%",
    )

    waste_company, waste_value = find_best_company(
        dataframe,
        "Waste_Waste_Recycling_%",
    )

    supplier_company, supplier_value = find_best_company(
        dataframe,
        "Supplier_Average_ESG_Score",
    )

    draw_kpi_card(
        figure,
        0.035,
        0.785,
        0.215,
        0.095,
        "COMPANIES ANALYSED",
        str(len(dataframe)),
        "Current reporting scope",
        GOVERNANCE_COLOR,
    )

    draw_kpi_card(
        figure,
        0.275,
        0.785,
        0.215,
        0.095,
        "RENEWABLE ENERGY LEADER",
        (
            f"{renewable_company} | "
            f"{format_percentage(renewable_value)}"
            if renewable_company
            else "N/A"
        ),
        "Highest renewable energy contribution",
        ENVIRONMENTAL_COLOR,
    )

    draw_kpi_card(
        figure,
        0.515,
        0.785,
        0.215,
        0.095,
        "WATER RECYCLING LEADER",
        (
            f"{water_company} | "
            f"{format_percentage(water_value)}"
            if water_company
            else "N/A"
        ),
        "Highest water recycling rate",
        ENVIRONMENTAL_COLOR,
    )

    draw_kpi_card(
        figure,
        0.755,
        0.785,
        0.215,
        0.095,
        "SUPPLIER ESG LEADER",
        (
            f"{supplier_company} | "
            f"{format_number(supplier_value)}"
            if supplier_company
            else "N/A"
        ),
        "Highest supplier ESG score",
        GOVERNANCE_COLOR,
    )

    # --------------------------------------------------------
    # Environmental Section
    # --------------------------------------------------------

    section = figure.add_axes(
        [0.035, 0.735, 0.93, 0.035]
    )

    section.axis("off")

    section.text(
        0,
        0.3,
        "01  ENVIRONMENTAL PERFORMANCE",
        fontsize=13,
        fontweight="bold",
        color=ENVIRONMENTAL_COLOR,
    )

    draw_percentage_chart(
        figure,
        dataframe,
        [
            "Energy_Renewable_Energy_%",
            "Water_Water_Recycling_%",
            "Waste_Waste_Recycling_%",
        ],
        [
            "Renewable Energy",
            "Water Recycling",
            "Waste Recycling",
        ],
        [0.055, 0.535, 0.42, 0.17],
        "Environmental Efficiency Indicators",
    )

    draw_comparison_chart(
        figure,
        dataframe,
        [
            "Energy_Total_Energy_kWh",
            "Water_Water_Used_m3",
        ],
        [
            "Total Energy",
            "Water Used",
        ],
        [0.525, 0.535, 0.42, 0.17],
        "Resource Consumption",
    )

    # --------------------------------------------------------
    # Social Section
    # --------------------------------------------------------

    section = figure.add_axes(
        [0.035, 0.475, 0.93, 0.035]
    )

    section.axis("off")

    section.text(
        0,
        0.3,
        "02  SOCIAL PERFORMANCE",
        fontsize=13,
        fontweight="bold",
        color=SOCIAL_COLOR,
    )

    draw_comparison_chart(
        figure,
        dataframe,
        [
            "HR_Average_Training_Hours",
            "Safety_Accident_Count",
        ],
        [
            "Training Hours",
            "Safety Incidents",
        ],
        [0.055, 0.275, 0.42, 0.17],
        "People Development & Workplace Safety",
    )

    draw_comparison_chart(
        figure,
        dataframe,
        [
            "HR_Employee_Count",
            "Safety_Lost_Days",
        ],
        [
            "Employees",
            "Lost Days",
        ],
        [0.525, 0.275, 0.42, 0.17],
        "Workforce Scale & Safety Impact",
    )

    # --------------------------------------------------------
    # Governance Section
    # --------------------------------------------------------

    section = figure.add_axes(
        [0.035, 0.215, 0.93, 0.035]
    )

    section.axis("off")

    section.text(
        0,
        0.3,
        "03  GOVERNANCE PERFORMANCE",
        fontsize=13,
        fontweight="bold",
        color=GOVERNANCE_COLOR,
    )

    draw_percentage_chart(
        figure,
        dataframe,
        [
            "Supplier_Average_ESG_Score",
            "Supplier_ISO14001_Certified_%",
            "Procurement_Average_Sustainable_Material_%",
        ],
        [
            "Supplier ESG Score",
            "ISO 14001 Certified",
            "Sustainable Procurement",
        ],
        [0.055, 0.025, 0.90, 0.17],
        "Responsible Supply Chain Indicators",
    )

    # --------------------------------------------------------
    # Executive Footer
    # --------------------------------------------------------

    footer = figure.add_axes(
        [0.035, 0.005, 0.93, 0.015]
    )

    footer.axis("off")

    footer.text(
        0,
        0,
        "ESG Analytics Platform | Management Reporting",
        fontsize=7,
        color=MUTED_COLOR,
    )

    figure.savefig(
        output_file,
        dpi=300,
        bbox_inches="tight",
        facecolor=BACKGROUND_COLOR,
    )

    plt.close(figure)

    return True


# ============================================================
# Main Visualization Pipeline
# ============================================================

def generate_all_visualizations(
    all_results,
    output_dir="outputs/visualizations",
):
    """Generate ESG charts, dashboard and comparison dataset."""

    output_path = Path(output_dir)

    output_path.mkdir(
        parents=True,
        exist_ok=True,
    )

    dataframe = prepare_kpi_dataframe(
        all_results
    )

    if dataframe.empty:
        raise ValueError(
            "No KPI data available for visualization."
        )

    # Environmental
    generate_environmental_charts(
        dataframe,
        output_path,
    )

    # Social
    generate_social_charts(
        dataframe,
        output_path,
    )

    # Governance
    generate_governance_charts(
        dataframe,
        output_path,
    )

    # Executive dashboard
    dashboard_created = (
        generate_management_dashboard(
            dataframe,
            output_path,
        )
    )

    if not dashboard_created:
        raise RuntimeError(
            "Unable to generate ESG management dashboard."
        )

    # Comparison CSV
    comparison_file = (
        output_path
        / "company_kpi_comparison.csv"
    )

    dataframe.to_csv(
        comparison_file,
        index=False,
    )

    return dataframe