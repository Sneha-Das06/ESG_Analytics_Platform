from io import BytesIO
import json
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd
from openpyxl import load_workbook

import database
from src.report_generator import (
    dataframe_csv_bytes,
    company_report_excel_bytes,
    company_report_pdf_bytes,
    create_company_report,
    manual_records_excel_bytes,
    manual_records_pdf_bytes,
    make_json_serializable,
)


class ReportExportTests(unittest.TestCase):
    def setUp(self):
        self.kpis = {
            "Energy": {"Total_Energy_kWh": 1000.0, "Renewable_Energy_%": 25.0},
            "Water": {"Water_Used_m3": 80.0, "Water_Recycling_%": 30.0},
            "Procurement": {"Average_Sustainable_Material_%": 35.4, "Sustainable_Material_Quantity": 3081.0},
        }
        self.report = create_company_report(
            "Green Future Industries", self.kpis,
            {"insights": ["Renewable contribution can improve."]},
            advanced_analysis={"score": 75.0}, reporting_period="2025-2026",
            validation={"Energy_Data": ["missing values found: Electricity_kWh=1"], "Water_Data": []},
        )
        self.report["generated_at_utc"] = "2026-11-13T00:00:00+00:00"
        self.chart_data = {
            "Energy": pd.DataFrame({"Reporting_Period": ["2025-04-01", "2025-05-01"], "Electricity_kWh": [100, 110], "Renewable_Energy_kWh": [25, 30]}),
            "Procurement": pd.DataFrame({"Reporting_Period": ["2025-04-01", "2025-05-01"], "Sustainable_Material_Quantity": [100, 120], "Sustainable_Material_Percent": [30, 35]}),
        }

    def test_excel_report_is_valid_and_contains_period_kpis_and_trend_tables(self):
        content = company_report_excel_bytes(self.report, self.chart_data)
        self.assertTrue(content.startswith(b"PK"))
        workbook = load_workbook(BytesIO(content), read_only=True, data_only=True)
        self.assertEqual(workbook.sheetnames, ["Report Info", "KPI Summary", "Analysis", "Data Quality", "Trend Energy", "Trend Procurement"])
        info = list(workbook["Report Info"].values)
        self.assertIn(("Company", "Reporting Period", "Report Type", "Generated At (UTC)"), info)
        self.assertIn(("Green Future Industries", "2025-2026", "ESG Analytics Report", "2026-11-13T00:00:00+00:00"), info)
        rows = list(workbook["KPI Summary"].values)
        self.assertIn(("Procurement", "Average_Sustainable_Material_%", 35.4, "%"), rows)
        self.assertIn(("Procurement", "Sustainable_Material_Quantity", 3081.0, "tonnes"), rows)
        procurement_rows = list(workbook["Trend Procurement"].values)
        self.assertEqual(procurement_rows[1][1], 100)
        self.assertEqual(procurement_rows[1][2], 30)
        quality_rows = list(workbook["Data Quality"].values)
        self.assertIn(("Energy_Data", "missing values found: Electricity_kWh=1"), quality_rows)
        self.assertIn(("Water_Data", "No validation issues reported"), quality_rows)
        workbook.close()

    def test_pdf_report_is_valid_paginated_and_includes_company_and_period(self):
        chart_data = {
            **self.chart_data,
            "Water": pd.DataFrame({"Reporting_Period": ["2025-04-01"], "Water_Consumed_m3": [80], "Recycled_Water_m3": [12]}),
            "Waste": pd.DataFrame({"Reporting_Period": ["2025-04-01"], "Total_Waste_Tonnes": [10], "Recycled_Waste_Tonnes": [4]}),
            "HR": pd.DataFrame({"Reporting_Period": ["2025-04-01"], "Training_Hours": [25]}),
            "Safety": pd.DataFrame({"Reporting_Period": ["2025-04-01"], "Recordable_Incidents": [1], "Lost_Days": [2]}),
            "Supplier": pd.DataFrame({
                "Supplier_ID": [f"Supplier regional identifier {index:02d} with long name" for index in range(1, 13)],
                "ESG_Score": np.linspace(60, 95, 12),
            }),
        }
        original_kpis = json.loads(json.dumps(self.report["kpis"]))
        from matplotlib.backends.backend_pdf import PdfPages
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        first_page = []
        captured_pages = []
        savefig = PdfPages.savefig

        def capture_first_page(pdf, figure, *args, **kwargs):
            captured_pages.append((figure, kwargs.copy()))
            if not first_page:
                first_page.append(figure)
            return savefig(pdf, figure, *args, **kwargs)

        with patch.object(PdfPages, "savefig", capture_first_page):
            content = company_report_pdf_bytes(self.report, chart_data)
        self.assertTrue(content.startswith(b"%PDF-"))
        self.assertEqual(len(re.findall(rb"/Type /Page\b", content)), 5)
        self.assertIn(b"Green Future Industries ESG Analytics Report", content)
        self.assertIn(b"Reporting period: 2025-2026", content)
        axis = first_page[0].axes[0]
        text_y = {item.get_text(): item.get_position()[1] for item in axis.texts}
        company_y = text_y["Green Future Industries"]
        title_y = text_y["ESG Analytics Report"]
        period_y = text_y["Reporting period: 2025-2026"]
        generated_y = text_y["Generated (UTC): 2026-11-13T00:00:00+00:00"]
        table = axis.tables[0]
        self.assertGreater(company_y, title_y)
        self.assertGreater(title_y, period_y)
        self.assertGreater(period_y, generated_y)
        self.assertLess(table._bbox[1] + table._bbox[3], generated_y)
        self.assertEqual(table[(0, 2)].get_text().get_ha(), "right")
        self.assertEqual(table[(1, 2)].get_text().get_ha(), "right")
        self.assertEqual(table[(1, 3)].get_text().get_ha(), "center")
        self.assertEqual(self.report["kpis"], original_kpis)

        chart_pages = [
            (figure, options) for figure, options in captured_pages
            if (
                figure._suptitle and "Source Data Trends" in figure._suptitle.get_text()
            ) or any(
                axis.get_title(loc="left") == "ESG Percentage Indicators"
                for axis in figure.axes
            )
        ]
        self.assertEqual(len(chart_pages), 4)  # PDF pages 2 through 5 contain charts.
        supplier_axis = next(
            axis for figure, _ in chart_pages for axis in figure.axes
            if axis.get_title(loc="left") == "Supplier ESG scores"
        )
        self.assertEqual(len(supplier_axis.lines[0].get_xdata()), 12)
        visible_supplier_ticks = [
            label.get_text() for label in supplier_axis.get_xticklabels() if label.get_visible()
        ]
        self.assertLessEqual(len(visible_supplier_ticks), 8)
        self.assertIn("Supplier regional identifier 01 with long name", visible_supplier_ticks)
        self.assertIn("Supplier regional identifier 12 with long name", visible_supplier_ticks)
        FigureCanvasAgg(supplier_axis.figure)
        supplier_axis.figure.canvas.draw()
        tick_bounds = [
            label.get_window_extent(supplier_axis.figure.canvas.get_renderer())
            for label in supplier_axis.get_xticklabels() if label.get_visible()
        ]
        visible_tick_texts = [
            label.get_text() for label in supplier_axis.get_xticklabels() if label.get_visible()
        ]
        for index, left in enumerate(tick_bounds):
            for right_index, right in enumerate(tick_bounds[index + 1:], start=index + 1):
                self.assertFalse(
                    left.overlaps(right),
                    f"Supplier tick labels overlap: {visible_tick_texts[index]!r} and {visible_tick_texts[right_index]!r}",
                )
        for figure, options in chart_pages:
            self.assertIsNone(options.get("bbox_inches"))
            self.assertEqual(tuple(round(value, 1) for value in figure.get_size_inches()), (11.7, 8.3))
            FigureCanvasAgg(figure)
            figure.canvas.draw()
            page = figure.bbox
            renderer = figure.canvas.get_renderer()
            chart_bounds = [
                axis.get_tightbbox(renderer)
                for axis in figure.axes
                if axis.axison
            ]
            self.assertTrue(chart_bounds)
            for bounds in chart_bounds:
                self.assertGreaterEqual(bounds.x0, page.x0)
                self.assertGreaterEqual(bounds.y0, page.y0)
                self.assertLessEqual(bounds.x1, page.x1)
                self.assertLessEqual(bounds.y1, page.y1)
            for index, left in enumerate(chart_bounds):
                for right in chart_bounds[index + 1:]:
                    self.assertFalse(left.overlaps(right), "Chart panels or their labels overlap.")

    def test_empty_missing_and_invalid_data_still_produce_valid_exports(self):
        empty_report = create_company_report("Empty Company", {}, {})
        invalid_chart = {"Energy": pd.DataFrame({"Reporting_Period": ["2025-04-01"], "Electricity_kWh": ["not numeric"]})}
        excel = company_report_excel_bytes(empty_report, invalid_chart)
        pdf = company_report_pdf_bytes(empty_report, invalid_chart)
        self.assertTrue(excel.startswith(b"PK"))
        self.assertTrue(pdf.startswith(b"%PDF-"))
        self.assertGreaterEqual(pdf.count(b"/Type /Page"), 1)
        self.assertTrue(manual_records_excel_bytes([]).startswith(b"PK"))
        self.assertTrue(manual_records_pdf_bytes([]).startswith(b"%PDF-"))
        self.assertTrue(manual_records_excel_bytes([{"financial_year": "FY2025", "energy_kwh": np.nan}]).startswith(b"PK"))
        sanitized = make_json_serializable(create_company_report("Invalid Company", {"Energy": {"Total_Energy_kWh": np.nan}}, {}))
        self.assertIn(b"null", json.dumps(sanitized, allow_nan=False).encode("utf-8"))

    def test_csv_download_bytes_are_utf8_and_contain_headers_and_exported_rows(self):
        frame = pd.DataFrame([{"Company": "Café Énergie", "Department": "Énergie", "Value": 42}])
        frame.loc[1] = {"Company": "Caf" + chr(233) + " " + chr(201) + "nergie", "Department": chr(201) + "nergie", "Value": 43}
        content = dataframe_csv_bytes(frame)
        self.assertIsInstance(content, bytes)
        self.assertIn(("Caf" + chr(233) + " " + chr(201) + "nergie").encode("utf-8"), content)
        self.assertEqual(content.splitlines()[0], b"Company,Department,Value")
        self.assertIn("Café Énergie".encode("utf-8"), content)
        parsed = pd.read_csv(BytesIO(content), encoding="utf-8")
        self.assertEqual(list(parsed.columns), ["Company", "Department", "Value"])
        self.assertEqual(parsed.iloc[0].to_dict(), {"Company": "Café Énergie", "Department": "Énergie", "Value": 42})


class ReportAccessTests(unittest.TestCase):
    def setUp(self):
        self.auth_env = patch.dict(os.environ, {"ESG_MFA_ENCRYPTION_KEY": database.Fernet.generate_key().decode()})
        self.auth_env.start()
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "reports.sqlite3"
        database.initialize_database(self.db)
        database.create_platform_admin("Platform", "platform@example.test", "PlatformPassword123!", self.db)
        self.platform = self._authenticate("platform@example.test", "PlatformPassword123!")
        _, self.company_a = database.register_company("Company A", "Manager A", "manager.a@example.test", "ManagerAPassword123!", path=self.db, company_size="Medium", country="India", official_email="office.a@example.test", terms_accepted=True, declaration_accepted=True)
        _, self.company_b = database.register_company("Company B", "Manager B", "manager.b@example.test", "ManagerBPassword123!", path=self.db, company_size="Large", country="India", official_email="office.b@example.test", terms_accepted=True, declaration_accepted=True)
        self.manager_a = self._authenticate("manager.a@example.test", "ManagerAPassword123!")
        self.manager_b = self._authenticate("manager.b@example.test", "ManagerBPassword123!")
        self.dept_a = database.create_department(self.manager_a, self.company_a, "Production", self.db)
        self.dept_a_other = database.create_department(self.manager_a, self.company_a, "Finance", self.db)
        self.dept_b = database.create_department(self.manager_b, self.company_b, "Operations", self.db)
        employee_id = database.register_user("Employee A", "employee.a@example.test", "EmployeeAPassword123!", "normal_user", self.company_a, self.dept_a, self.db)
        database.approve_user(self.manager_a, employee_id, True, self.dept_a, False, path=self.db)
        self.employee = database.authenticate("employee.a@example.test", "EmployeeAPassword123!", self.db)
        pending_id = database.register_user("Pending Auditor", "pending.auditor@example.test", "PendingAuditorPassword123!", "viewer", path=self.db)
        self.pending_viewer = {"id": pending_id}
        viewer_id = database.register_user("Auditor", "auditor@example.test", "AuditorPassword123!", "viewer", path=self.db)
        database.approve_user(self.platform, viewer_id, True, path=self.db)
        self.viewer = database.authenticate("auditor@example.test", "AuditorPassword123!", self.db)

        database.save_esg_record(self.manager_a, self.company_a, self.dept_a, "FY2025", 100, 20, 8, 4, 30, 85, path=self.db)
        database.save_esg_record(self.manager_a, self.company_a, self.dept_a_other, "FY2025", 200, 30, 9, 5, 40, 80, path=self.db)
        database.save_esg_record(self.manager_b, self.company_b, self.dept_b, "FY2025", 300, 40, 10, 6, 50, 75, path=self.db)
        database.import_department_rows(self.manager_a, self.dept_a, "a.csv", "hash-a", [{"safe": "A"}], path=self.db)
        database.import_department_rows(self.manager_a, self.dept_a_other, "a2.csv", "hash-a2", [{"safe": "A2"}], path=self.db)
        database.import_department_rows(self.manager_b, self.dept_b, "b.csv", "hash-b", [{"safe": "B"}], path=self.db)

    def tearDown(self):
        self.temp.cleanup()
        self.auth_env.stop()

    def _authenticate(self, email, password):
        user = database.authenticate(email, password, self.db, client_ip="report-test")
        if user and user.get("mfa_enrollment_required"):
            with database.connect(self.db) as conn:
                encrypted = conn.execute("SELECT secret_enc FROM mfa_enrollment_challenges WHERE user_id=?", (user["id"],)).fetchone()[0]
            secret = database._mfa_cipher().decrypt(encrypted.encode()).decode()
            return database.complete_mfa_enrollment(user["id"], user["mfa_enrollment_token"], database._totp_code(secret), self.db)["user"]
        return user

    def test_company_managers_are_scoped_to_own_company_reports(self):
        self.assertEqual([row["id"] for row in database.list_companies(self.manager_a, path=self.db)], [self.company_a])
        self.assertEqual({row["company_id"] for row in database.list_esg_records(self.manager_a, path=self.db)}, {self.company_a})
        self.assertEqual({row["company_id"] for row in database.list_legacy_rows(self.manager_a, limit=100, path=self.db)}, {self.company_a})
        with self.assertRaises(PermissionError):
            database.list_esg_records(self.manager_a, self.company_b, self.db)
        with self.assertRaises(PermissionError):
            database.list_legacy_rows(self.manager_a, company_id=self.company_b, path=self.db)
        self.assertEqual({row["company_id"] for row in database.list_esg_records(self.manager_b, path=self.db)}, {self.company_b})

    def test_normal_user_report_data_is_limited_to_assigned_department(self):
        records = database.list_esg_records(self.employee, path=self.db)
        legacy = database.list_legacy_rows(self.employee, company_id=self.company_a, limit=100, path=self.db)
        self.assertEqual(len(records), 1)
        self.assertEqual({row["department_id"] for row in records}, {self.dept_a})
        self.assertEqual(len(legacy), 1)
        self.assertEqual({row["department_id"] for row in legacy}, {self.dept_a})
        with self.assertRaises(PermissionError):
            database.list_esg_records(self.employee, self.company_b, self.db)

    def test_only_approved_viewer_can_read_cross_company_reports(self):
        with self.assertRaises(PermissionError):
            database.list_esg_records(self.pending_viewer, path=self.db)
        self.assertCountEqual(database.accessible_company_ids(self.viewer, path=self.db), [self.company_a, self.company_b])
        self.assertEqual(len(database.list_esg_records(self.viewer, path=self.db)), 3)
        self.assertEqual(len(database.list_legacy_rows(self.viewer, limit=100, path=self.db)), 3)

    def test_viewer_downloads_are_built_only_from_authorized_report_rows(self):
        authorized = database.list_esg_records(self.viewer, company_id=self.company_a, path=self.db)
        self.assertEqual({row["company_id"] for row in authorized}, {self.company_a})
        self.assertTrue(manual_records_excel_bytes(authorized).startswith(b"PK"))
        self.assertTrue(manual_records_pdf_bytes(authorized).startswith(b"%PDF-"))
        with self.assertRaises(PermissionError):
            database.list_esg_records(self.viewer, company_id=999999, path=self.db)

    def test_platform_super_admin_has_no_esg_report_data_access(self):
        self.assertEqual(database.list_esg_records(self.platform, path=self.db), [])
        self.assertEqual(database.list_legacy_rows(self.platform, limit=100, path=self.db), [])
        self.assertEqual({row["id"] for row in database.list_companies(self.platform, path=self.db)}, {self.company_a, self.company_b})

    def test_report_reads_and_exports_do_not_lock_each_other(self):
        with database.connect(self.db) as reader:
            reader.execute("BEGIN")
            reader.execute("SELECT COUNT(*) FROM esg_records").fetchone()
            records = database.list_esg_records(self.manager_a, path=self.db)
            self.assertEqual(len(records), 2)
            report = create_company_report("Company A", {"Energy": {"Total_Energy_kWh": 300}}, {}, reporting_period="2025-2026")
            self.assertTrue(company_report_excel_bytes(report).startswith(b"PK"))
            self.assertTrue(company_report_pdf_bytes(report).startswith(b"%PDF-"))
            reader.rollback()


if __name__ == "__main__":
    unittest.main()

