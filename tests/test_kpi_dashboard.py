import unittest

import pandas as pd
import plotly.express as px

from src.data_loader import EXPECTED_SHEETS
from src.kpi_calculator import calculate_kpis
from src.kpi_dashboard import build_kpi_chart_data, style_kpi_figure
from src.preprocessing import preprocess_company_data


class KPIDashboardTests(unittest.TestCase):
    def test_kpi_chart_style_has_high_contrast_titles_axes_legends_and_hover(self):
        frame = pd.DataFrame({"Reporting_Period": ["Q1", "Q2"], "Energy_kWh": [100, 120], "Solar_kWh": [20, 30]})
        figures = [
            px.line(frame, x="Reporting_Period", y=["Energy_kWh", "Solar_kWh"], title="Energy trend"),
            px.bar(frame, x="Reporting_Period", y="Energy_kWh", title="Water, waste, or procurement measure"),
            px.bar(pd.DataFrame({"Department": ["Ops", "HR"], "Employees": [4, 6]}), x="Department", y="Employees", title="Employees by department"),
        ]
        for figure in figures:
            styled = style_kpi_figure(figure)
            self.assertEqual(styled.layout.paper_bgcolor, "#ffffff")
            self.assertEqual(styled.layout.plot_bgcolor, "#ffffff")
            self.assertEqual(styled.layout.title.font.color, "#0b1f33")
            self.assertEqual(styled.layout.title.font.weight, 700)
            self.assertEqual(styled.layout.xaxis.title.font.color, "#0b1f33")
            self.assertEqual(styled.layout.xaxis.tickfont.color, "#243f52")
            self.assertEqual(styled.layout.yaxis.title.font.color, "#0b1f33")
            self.assertEqual(styled.layout.yaxis.tickfont.color, "#243f52")
            self.assertEqual(styled.layout.yaxis.gridcolor, "#d5e0e5")
            self.assertEqual(styled.layout.legend.font.color, "#243f52")
            self.assertEqual(styled.layout.hoverlabel.bgcolor, "#0b1f33")
            self.assertEqual(styled.layout.hoverlabel.font.color, "#ffffff")

    def test_all_kpi_category_chart_types_receive_the_same_style(self):
        frame = pd.DataFrame({"Reporting_Period": ["Q1", "Q2"], "Measure": [10, 12], "Other": [4, 5]})
        category_figures = {
            "Energy": [px.line(frame, x="Reporting_Period", y=["Measure", "Other"]), px.bar(frame, x="Reporting_Period", y="Measure")],
            "Water": [px.bar(frame, x="Reporting_Period", y="Measure")],
            "Waste": [px.bar(frame, x="Reporting_Period", y="Measure")],
            "HR": [px.bar(frame, x="Reporting_Period", y="Measure"), px.bar(frame, x="Reporting_Period", y="Other")],
            "Safety": [px.bar(frame, x="Reporting_Period", y="Measure"), px.line(frame, x="Reporting_Period", y="Other")],
            "Supplier": [px.bar(frame, x="Reporting_Period", y="Measure")],
            "Procurement": [px.bar(frame, x="Reporting_Period", y="Measure"), px.line(frame, x="Reporting_Period", y="Other")],
        }
        self.assertEqual(set(category_figures), {"Energy", "Water", "Waste", "HR", "Safety", "Supplier", "Procurement"})
        for category, figures in category_figures.items():
            with self.subTest(category=category):
                for figure in figures:
                    style_kpi_figure(figure)
                    self.assertEqual(figure.layout.title.font.color, "#0b1f33")
                    self.assertEqual(figure.layout.hoverlabel.font.color, "#ffffff")

    def test_preprocessed_sustainable_material_percent_is_used(self):
        source = {
            EXPECTED_SHEETS[0]: pd.DataFrame([{"Electricity_kWh": 100, "Renewable_Energy_kWh": 20}]),
            EXPECTED_SHEETS[1]: pd.DataFrame([{"Water_Consumed_m3": 10, "Recycled_Water_m3": 2}]),
            EXPECTED_SHEETS[2]: pd.DataFrame([{"Total_Waste_Tonnes": 4, "Recycled_Waste_Tonnes": 1}]),
            EXPECTED_SHEETS[3]: pd.DataFrame([{"Employee_ID": 1, "Department": "Operations", "Training_Hours": 2}]),
            EXPECTED_SHEETS[4]: pd.DataFrame([{"Recordable_Incidents": 1}, {"Recordable_Incidents": 0}]),
            EXPECTED_SHEETS[5]: pd.DataFrame([{"Supplier_ID": 1, "ESG_Score": 80, "ISO14001_Certified": 1}]),
            EXPECTED_SHEETS[6]: pd.DataFrame([{"Sustainable_Material_%": 30, "Sustainable_Material_Quantity": 10}, {"Sustainable_Material_%": 50, "Sustainable_Material_Quantity": 20}]),
        }
        processed = preprocess_company_data(source)
        kpis = calculate_kpis(processed)
        chart_data = build_kpi_chart_data(processed)

        self.assertEqual(kpis["Procurement"]["Average_Sustainable_Material_%"], 40)
        self.assertEqual(kpis["Procurement"]["Sustainable_Material_Quantity"], 30)
        self.assertEqual(kpis["Safety"]["Accident_Count"], 1)
        self.assertIn("Sustainable_Material_Percent", chart_data["Procurement"].columns)
        self.assertNotIn("Employee_ID", chart_data["HR"].columns)
        self.assertEqual(chart_data["HR_Employees_By_Department"]["Employees"].sum(), 1)


if __name__ == "__main__":
    unittest.main()

