import json
import math
import re
from datetime import date, datetime
from pathlib import Path

import numpy as np


def dataframe_csv_bytes(frame):
    """Serialize an already-authorized DataFrame as UTF-8 CSV download bytes."""
    return frame.to_csv(index=False).encode("utf-8")


def make_json_serializable(value):
    """Convert NumPy, Pandas, and date values to JSON-safe values."""

    if isinstance(value, dict):
        return {
            str(key): make_json_serializable(item)
            for key, item in value.items()
        }

    if isinstance(value, list):
        return [
            make_json_serializable(item)
            for item in value
        ]

    if isinstance(value, tuple):
        return [
            make_json_serializable(item)
            for item in value
        ]

    if isinstance(value, np.integer):
        return int(value)

    if isinstance(value, np.floating):
        number = float(value)
        return number if math.isfinite(number) else None

    if isinstance(value, float) and not math.isfinite(value):
        return None

    if value.__class__.__name__ == "NAType":
        return None

    if isinstance(value, np.bool_):
        return bool(value)

    if isinstance(value, np.ndarray):
        return [
            make_json_serializable(item)
            for item in value.tolist()
        ]

    if isinstance(value, (datetime, date)):
        return value.isoformat()

    return value


def create_company_report(
    company_name,
    kpis,
    analysis,
    advanced_analysis=None,
    reporting_period=None,
    validation=None,
):
    """Create a structured ESG report dictionary."""
    report = {
        "company": company_name,
        "report_type": "ESG Analytics Report",
        "kpis": kpis,
        "analysis": analysis,
    }
    if advanced_analysis is not None:
        report["advanced_analysis"] = advanced_analysis
    if reporting_period is not None:
        report["reporting_period"] = reporting_period
    if validation is not None:
        report["validation"] = validation
    return report


def create_safe_filename(company_name):
    """Create a filesystem-safe company report filename."""

    safe_name = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(company_name).strip().lower()).strip("_-") or "company"

    return f"{safe_name}_esg_report.json"


def save_company_report(
    company_name,
    kpis,
    analysis,
    output_dir,
):
    """Save a company ESG report as a structured JSON file."""

    output_path = Path(output_dir)

    output_path.mkdir(
        parents=True,
        exist_ok=True,
    )

    report = create_company_report(
        company_name,
        kpis,
        analysis,
    )

    report = make_json_serializable(
        report
    )

    output_file = (
        output_path
        / create_safe_filename(company_name)
    )

    try:
        with open(
            output_file,
            "w",
            encoding="utf-8",
        ) as file:

            json.dump(
                report,
                file,
                indent=4,
                ensure_ascii=False,
            )

    except OSError as error:
        raise OSError(
            f"Unable to save ESG report for "
            f"{company_name}: {error}"
        ) from error

    return output_file

KPI_UNITS = {
    "Electricity_kWh": "kWh", "Diesel_Litres": "L", "Solar_kWh": "kWh",
    "Renewable_Energy_kWh": "kWh", "Total_Energy_kWh": "kWh",
    "Renewable_Energy_%": "%", "Water_Used_m3": "m3", "Water_Recycling_%": "%",
    "Recycled_Water_m3": "m3", "Total_Waste_Tonnes": "tonnes",
    "Recycled_Waste_Tonnes": "tonnes", "Hazardous_Waste_Tonnes": "tonnes",
    "Waste_Recycling_%": "%", "Employee_Count": "employees",
    "Total_Training_Hours": "hours", "Average_Training_Hours": "hours/record",
    "Accident_Count": "incidents", "Lost_Days": "days",
    "Affected_Employees": "employees", "Supplier_Count": "suppliers",
    "Average_ESG_Score": "score / 100", "ISO14001_Certified_%": "%",
    "Purchase_Amount": "INR", "Average_Sustainable_Material_%": "%",
    "Sustainable_Material_Quantity": "tonnes",
}


def _kpi_rows(kpis):
    rows = []
    for category, values in (kpis or {}).items():
        if not isinstance(values, dict):
            continue
        for name, value in values.items():
            if isinstance(value, (dict, list, tuple)):
                continue
            rows.append({
                "Category": str(category), "KPI": str(name),
                "Value": make_json_serializable(value), "Unit": KPI_UNITS.get(str(name), ""),
            })
    return rows


def _flatten_section(section, value, output):
    if isinstance(value, dict):
        for key, item in value.items():
            _flatten_section(section, item, output + [str(key)])
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value, start=1):
            _flatten_section(section, item, output + [str(index)])
    else:
        output.append({"Section": section, "Field": ".".join(output), "Value": make_json_serializable(value)})


def company_report_excel_bytes(report, chart_data=None):
    """Build an in-memory, structured XLSX report with KPI and source trend tables."""
    import pandas as pd
    from io import BytesIO

    output = BytesIO()
    info = pd.DataFrame([{
        "Company": report.get("company", ""),
        "Reporting Period": report.get("reporting_period") or "Not specified",
        "Report Type": report.get("report_type", "ESG Analytics Report"),
        "Generated At (UTC)": report.get("generated_at_utc", ""),
    }])
    kpis = pd.DataFrame(_kpi_rows(report.get("kpis", {})), columns=["Category", "KPI", "Value", "Unit"])
    details = []
    for section in ("analysis", "advanced_analysis"):
        if report.get(section):
            _flatten_section(section.replace("_", " ").title(), report[section], details)
    analysis = pd.DataFrame(details, columns=["Section", "Field", "Value"])
    validation = report.get("validation")
    quality_rows = []
    if isinstance(validation, dict):
        for sheet, issues in validation.items():
            if isinstance(issues, (list, tuple)) and issues:
                quality_rows.extend({"Sheet": str(sheet), "Issue": str(issue)} for issue in issues)
            else:
                quality_rows.append({"Sheet": str(sheet), "Issue": "No validation issues reported"})
    quality = pd.DataFrame(quality_rows, columns=["Sheet", "Issue"])
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        info.to_excel(writer, index=False, sheet_name="Report Info")
        kpis.to_excel(writer, index=False, sheet_name="KPI Summary")
        analysis.to_excel(writer, index=False, sheet_name="Analysis")
        if isinstance(validation, dict):
            quality.to_excel(writer, index=False, sheet_name="Data Quality")
        for category, frame in (chart_data or {}).items():
            if frame is None or not hasattr(frame, "to_excel") or frame.empty:
                continue
            safe = re.sub(r"[^A-Za-z0-9 ]+", "", str(category)).strip()[:25] or "Trend Data"
            sheet = ("Trend " + safe)[:31]
            frame.to_excel(writer, index=False, sheet_name=sheet)
    return output.getvalue()


def _trend_specs(chart_data):
    specs = []
    definitions = (
        ("Energy", ("Electricity_kWh", "Renewable_Energy_kWh"), "Monthly energy use", "kWh"),
        ("Energy", ("Carbon_Emissions_tCO2e",), "Monthly carbon emissions", "tCO2e"),
        ("Water", ("Water_Consumed_m3", "Water_Used_m3", "Recycled_Water_m3"), "Monthly water use and recycling", "m3"),
        ("Waste", ("Total_Waste_Tonnes", "Recycled_Waste_Tonnes", "Hazardous_Waste_Tonnes"), "Monthly waste generation", "tonnes"),
        ("HR", ("Training_Hours",), "Training hours by reporting period", "hours"),
        ("Safety", ("Recordable_Incidents",), "Recordable incidents by reporting period", "incidents"),
        ("Safety", ("Lost_Days",), "Lost workdays by reporting period", "days"),
        ("Supplier", ("ESG_Score",), "Supplier ESG scores", "score / 100"),
        ("Procurement", ("Sustainable_Material_Quantity",), "Sustainable material quantity", "tonnes"),
        ("Procurement", ("Sustainable_Material_Percent", "Sustainable_Material_%"), "Sustainable material share", "%"),
    )
    for category, candidates, title, unit in definitions:
        frame = (chart_data or {}).get(category)
        if frame is None or frame.empty:
            continue
        available = [column for column in candidates if column in frame.columns]
        if not available:
            continue
        x_column = "Reporting_Period" if "Reporting_Period" in frame.columns else "Supplier_ID" if "Supplier_ID" in frame.columns else None
        if category == "HR" and x_column == "Reporting_Period":
            frame = frame.groupby(x_column, as_index=False)[available[0]].sum(min_count=1)
        if category == "Supplier" and x_column is None:
            frame = frame.copy()
            frame.insert(0, "Supplier_ID", range(1, len(frame) + 1))
            x_column = "Supplier_ID"
        specs.append((category, frame, available, x_column, title, unit))
    return specs


def company_report_pdf_bytes(report, chart_data=None):
    """Build a paginated PDF with a readable KPI table and available source trends."""
    import pandas as pd
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages
    from io import BytesIO

    output = BytesIO()
    rows = _kpi_rows(report.get("kpis", {}))
    with PdfPages(output) as pdf:
        metadata = pdf.infodict()
        metadata["Title"] = f"{report.get('company', 'ESG Report')} ESG Analytics Report"
        metadata["Subject"] = f"Reporting period: {report.get('reporting_period') or 'Not specified'}"
        figure, axis = plt.subplots(figsize=(11.7, 8.3))
        axis.axis("off")
        axis.text(0.04, 0.96, str(report.get("company", "ESG Report")), fontsize=19, fontweight="bold", va="top", color="#0b1f33")
        period = report.get("reporting_period") or "Not specified"
        generated = report.get("generated_at_utc", "")
        axis.text(0.04, 0.88, "ESG Analytics Report", fontsize=11, va="top", color="#334b5d")
        axis.text(0.04, 0.83, f"Reporting period: {period}", fontsize=9, va="top", color="#334b5d")
        axis.text(0.04, 0.78, f"Generated (UTC): {generated}", fontsize=9, va="top", color="#334b5d")
        if rows:
            table = axis.table(cellText=[[row["Category"], row["KPI"], "" if row["Value"] is None else str(row["Value"]), row["Unit"]] for row in rows], colLabels=["Category", "KPI", "Value", "Unit"], cellLoc="left", colWidths=[0.18, 0.40, 0.20, 0.18], bbox=[0.04, 0.07, 0.92, 0.62])
            table.auto_set_font_size(False)
            table.set_fontsize(8)
            for column in range(4):
                header = table[(0, column)]
                header.get_text().set_ha("right" if column == 2 else "center" if column == 3 else "left")
            for row_index in range(1, len(rows) + 1):
                table[(row_index, 2)].get_text().set_ha("right")
                table[(row_index, 3)].get_text().set_ha("center")
            for cell in table.get_celld().values():
                cell.set_height(0.034)
        else:
            axis.text(0.04, 0.68, "No KPI data is available for this reporting period.", fontsize=12, color="#657873")
        pdf.savefig(figure, bbox_inches="tight")
        plt.close(figure)

        percentage_rows = [row for row in rows if row["Unit"] == "%" and isinstance(row["Value"], (int, float)) and math.isfinite(float(row["Value"]))]
        if percentage_rows:
            figure, axis = plt.subplots(figsize=(11.7, 8.3), layout="constrained")
            figure.get_layout_engine().set(w_pad=0.20, h_pad=0.20)
            labels = [f"{row['Category']} - {row['KPI']}" for row in percentage_rows]
            values = [float(row["Value"]) for row in percentage_rows]
            axis.barh(labels, values, color="#16856f")
            axis.set_xlim(0, max(100, max(values, default=0) * 1.08 + 1.0))
            axis.set_xlabel("Percent")
            axis.set_title("ESG Percentage Indicators", loc="left", fontsize=15, color="#0b1f33", pad=16)
            axis.grid(axis="x", alpha=0.2)
            for index, value in enumerate(values):
                axis.text(value + 0.7, index, f"{value:.1f}%", va="center", fontsize=9)
            pdf.savefig(figure)
            plt.close(figure)

        specs = _trend_specs(chart_data)
        for start in range(0, len(specs), 4):
            chunk = specs[start:start + 4]
            figure, axes = plt.subplots(
                2, 2, figsize=(11.7, 8.3), layout="constrained"
            )
            figure.get_layout_engine().set(
                w_pad=0.20, h_pad=0.20, wspace=0.12, hspace=0.16
            )
            figure.suptitle(f"{report.get('company', 'ESG Report')} - Source Data Trends", fontsize=15, fontweight="bold", color="#0b1f33")
            for axis, (category, frame, columns, x_column, title, unit) in zip(axes.flat, chunk):
                if x_column:
                    x = frame[x_column].astype(str)
                    x_label = "Reporting period" if x_column == "Reporting_Period" else "Supplier ID"
                else:
                    x = pd.Series(range(1, len(frame) + 1), index=frame.index).astype(str)
                    x_label = "Record"
                drew = False
                for column in columns:
                    values = pd.to_numeric(frame[column], errors="coerce")
                    valid = values.notna()
                    if valid.any():
                        axis.plot(x.loc[valid], values.loc[valid], marker="o", linewidth=1.8, label=column)
                        drew = True
                axis.set_title(title, loc="left", fontsize=10, color="#0b1f33")
                axis.set_ylabel(unit)
                axis.set_xlabel(x_label)
                axis.margins(x=0.08)
                axis.tick_params(axis="x", labelrotation=35, labelsize=7)
                tick_labels = axis.get_xticklabels()
                longest_tick = max((len(label.get_text()) for label in tick_labels), default=0)
                max_visible_ticks = 3 if longest_tick > 32 else 5 if longest_tick > 20 else 8
                if len(tick_labels) > max_visible_ticks:
                    visible_labels = set(
                        np.linspace(
                            0, len(tick_labels) - 1, num=max_visible_ticks, dtype=int
                        ).tolist()
                    )
                    for index, tick_label in enumerate(tick_labels):
                        tick_label.set_visible(index in visible_labels)
                for tick_label in tick_labels:
                    if not tick_label.get_visible():
                        continue
                    if longest_tick > 32:
                        tick_label.set_fontsize(5.5)
                    elif longest_tick > 20:
                        tick_label.set_fontsize(6)
                    tick_label.set_horizontalalignment("right")
                    tick_label.set_rotation_mode("anchor")
                axis.grid(alpha=0.2)
                if drew and len(columns) > 1:
                    axis.legend(fontsize=7)
                elif not drew:
                    axis.text(0.5, 0.5, "No valid numeric values", ha="center", va="center", transform=axis.transAxes)
            for axis in axes.flat[len(chunk):]:
                axis.axis("off")
            # Keep the chart page at its designed landscape page size. The
            # constrained layout reserves room for ticks, labels, and titles;
            # tight bounding-box export could crop or resize the page edge.
            pdf.savefig(figure)
            plt.close(figure)

        detail_rows = []
        for section in ("analysis", "advanced_analysis", "validation"):
            if report.get(section):
                _flatten_section(section.replace("_", " ").title(), report[section], detail_rows)
        for start in range(0, len(detail_rows), 24):
            chunk = detail_rows[start:start + 24]
            figure, axis = plt.subplots(figsize=(11.7, 8.3))
            axis.axis("off")
            axis.set_title("Analysis and Findings", loc="left", fontsize=15, fontweight="bold", color="#0b1f33", pad=18)
            table = axis.table(cellText=[[row["Section"], row["Field"], "" if row["Value"] is None else str(row["Value"])] for row in chunk], colLabels=["Section", "Finding", "Value"], loc="center", cellLoc="left", colWidths=[0.20, 0.48, 0.30])
            table.auto_set_font_size(False)
            table.set_fontsize(8)
            for cell in table.get_celld().values():
                cell.set_height(0.034)
            pdf.savefig(figure, bbox_inches="tight")
            plt.close(figure)
    return output.getvalue()


def manual_records_excel_bytes(records):
    """Build an in-memory Excel export of only records already authorized by the caller."""
    import pandas as pd
    from io import BytesIO
    frame = pd.DataFrame(list(records or []))
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        frame.to_excel(writer, index=False, sheet_name="ESG Records")
    return output.getvalue()


def manual_records_pdf_bytes(records):
    """Build a paginated PDF table from authorized manual ESG records."""
    import pandas as pd
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages
    from io import BytesIO
    frame = pd.DataFrame(list(records or []))
    output = BytesIO()
    columns = [("financial_year", "Financial Year"), ("energy_kwh", "Energy kWh"), ("water_m3", "Water m3"), ("carbon_tco2e", "Carbon tCO2e"), ("waste_tonnes", "Waste tonnes"), ("renewable_pct", "Renewable %"), ("social_score", "Social score")]
    available = [(source, label) for source, label in columns if source in frame.columns]
    with PdfPages(output) as pdf:
        groups = frame.groupby([column for column in ("company_name", "department_name") if column in frame.columns], dropna=False) if not frame.empty and any(column in frame.columns for column in ("company_name", "department_name")) else [("Authorized records", frame)]
        for group_key, part in groups:
            group_label = " - ".join(map(str, group_key if isinstance(group_key, tuple) else (group_key,)))
            if part.empty:
                continue
            for start in range(0, len(part), 28):
                chunk = part.iloc[start:start + 28]
                figure, axis = plt.subplots(figsize=(11.7, 8.3))
                axis.axis("off")
                axis.set_title(f"{group_label} - ESG Performance", loc="left", fontsize=15, fontweight="bold", color="#0b1f33", pad=18)
                if available:
                    table = axis.table(cellText=chunk[[source for source, _ in available]].fillna("").values, colLabels=[label for _, label in available], loc="center", cellLoc="center")
                    table.auto_set_font_size(False)
                    table.set_fontsize(7)
                    table.scale(1, 1.25)
                pdf.savefig(figure, bbox_inches="tight")
                plt.close(figure)
        if frame.empty:
            figure, axis = plt.subplots(figsize=(11.7, 8.3))
            axis.axis("off")
            axis.text(0.03, 0.95, "No manual ESG records are available.", fontsize=12, va="top")
            pdf.savefig(figure, bbox_inches="tight")
            plt.close(figure)
    return output.getvalue()
