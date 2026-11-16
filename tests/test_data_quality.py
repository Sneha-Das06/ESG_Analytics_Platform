import unittest

import pandas as pd

from src.data_quality import analyze_frame, apply_cleaning


class DataQualityTests(unittest.TestCase):
    def test_detects_exact_and_configured_business_duplicates_without_fuzzy_matching(self):
        frame = pd.DataFrame({
            "Company Name": ["Acme", "Acme", "Acme Inc", "ACME"],
            "Financial Year": ["FY2024", "FY2024", "FY2024", "FY2024"],
            "Energy_kWh": [10, 10, 20, 30],
        })
        report = analyze_frame(frame, business_keys=["Company Name", "Financial Year"])
        self.assertEqual(report["metrics"]["exact_duplicate_records"], 1)
        self.assertEqual(report["metrics"]["business_key_duplicate_records"], 1)
        self.assertEqual(report["metrics"]["duplicate_records"], 1)
        # A case-only spelling variant is reported for manual review but is not a duplicate.
        name_report = analyze_frame(frame, business_keys=["Company Name"])
        self.assertEqual(name_report["metrics"]["business_key_duplicate_records"], 1)
        self.assertTrue(name_report["company_name_variants"])
        self.assertEqual(len(frame), 4)

    def test_fully_empty_rows_are_distinct_from_partial_rows_and_require_confirmation(self):
        frame = pd.DataFrame({
            "Department": ["Energy", "  ", "Water", "Energy", None],
            "Energy_kWh": [10, None, None, 10, None],
        })
        report = analyze_frame(frame)
        self.assertEqual(report["metrics"]["empty_rows"], 2)
        self.assertGreater(report["metrics"]["missing_values"], 0)
        self.assertEqual(report["metrics"]["exact_duplicate_records"], 1)
        # Analysis and preview never mutate the source or remove rows.
        self.assertEqual(len(report["original_frame"]), 5)
        self.assertEqual(len(frame), 5)
        with self.assertRaisesRegex(ValueError, "Confirm the cleaning preview"):
            apply_cleaning(frame, remove_empty=True, remove_exact_duplicates=True)
        cleaned, details, _ = apply_cleaning(frame, remove_empty=True, remove_exact_duplicates=True, confirmed=True)
        self.assertEqual(len(cleaned), 2)
        self.assertEqual(details["removed_records"], 3)
        self.assertEqual(details["skipped_records"], 0)
        # Partially completed Water row is retained for review.
        self.assertTrue((cleaned["Department"] == "Water").any())

    def test_whitespace_normalization_and_missing_values_are_previewed(self):
        frame = pd.DataFrame({"Supplier Name": ["  Green   Supply ", " ", "Other"], "Value": [1, None, None]})
        normalized, changed, report = apply_cleaning(frame)
        self.assertEqual(normalized.iloc[0]["Supplier Name"], "Green Supply")
        self.assertEqual(report["metrics"]["cleaned_records"], 2)
        self.assertEqual(report["metrics"]["empty_rows"], 1)
        self.assertGreater(report["metrics"]["missing_values"], 0)
        self.assertEqual(len(frame), 3)
        self.assertTrue(frame.iloc[0]["Supplier Name"].startswith("  "))

    def test_partial_records_are_never_removed_as_duplicates(self):
        frame = pd.DataFrame({"Company": ["A", "A"], "Financial Year": ["FY2025", "FY2025"], "Energy_kWh": [10, None]})
        with self.assertRaisesRegex(ValueError, "Confirm the cleaning preview"):
            apply_cleaning(frame, remove_exact_duplicates=True,
                           business_keys=["Company", "Financial Year"],
                           remove_business_key_duplicates=True)
        cleaned, details, _ = apply_cleaning(frame, remove_exact_duplicates=True,
                                             business_keys=["Company", "Financial Year"],
                                             remove_business_key_duplicates=True, confirmed=True)
        self.assertEqual(len(cleaned), 2)
        self.assertEqual(details["removed_records"], 0)
        self.assertEqual(details["skipped_records"], 1)

    def test_exact_duplicate_removal_confirmation_and_incomplete_retention(self):
        frame = pd.DataFrame({"Year": [2025, 2025, 2025], "Water_m3": [10, 10, None]})
        preview = analyze_frame(frame)
        self.assertEqual(preview["metrics"]["exact_duplicate_records"], 1)
        self.assertEqual(preview["metrics"]["incomplete_duplicate_records"], 0)
        original = frame.copy(deep=True)
        with self.assertRaisesRegex(ValueError, "Confirm the cleaning preview"):
            apply_cleaning(frame, remove_exact_duplicates=True)
        self.assertTrue(frame.equals(original))

        duplicate = pd.DataFrame({"Year": [2025, 2025], "Water_m3": [10, 10]})
        cleaned, details, _ = apply_cleaning(duplicate, remove_exact_duplicates=True, confirmed=True)
        self.assertEqual(len(cleaned), 1)
        self.assertEqual(details["removed_records"], 1)
        self.assertEqual(details["cleaned_records"], 0)

        incomplete = pd.DataFrame({"Year": [2025, 2025], "Water_m3": [None, None]})
        report = analyze_frame(incomplete)
        self.assertEqual(report["metrics"]["incomplete_duplicate_records"], 1)
        retained, details, _ = apply_cleaning(incomplete, remove_exact_duplicates=True, confirmed=True)
        self.assertEqual(len(retained), 2)
        self.assertEqual(details["removed_records"], 0)
        self.assertEqual(details["skipped_records"], 1)

    def test_invalid_numeric_value_is_flagged_and_not_silently_removed(self):
        frame = pd.DataFrame({"Energy_kWh": [12, "unknown"], "Year": [2025, 2025]})
        cleaned, details, report = apply_cleaning(frame)
        self.assertEqual(report["metrics"]["invalid_records"], 1)
        self.assertEqual(report["metrics"]["flagged_records"], 1)
        self.assertEqual(len(cleaned), 2)
        self.assertEqual(details["removed_records"], 0)
        self.assertIn("unknown", cleaned["Energy_kWh"].tolist())

    def test_cleaned_records_counts_safe_changes_separately_from_removals(self):
        frame = pd.DataFrame({"Supplier": ["  Green   Supply", "Acme", "Acme"], "Value": [1, 2, 2]})
        cleaned, details, _ = apply_cleaning(frame, remove_exact_duplicates=True, confirmed=True)
        self.assertEqual(details["cleaned_records"], 1)
        self.assertEqual(details["changed_records"], 1)
        self.assertEqual(details["removed_records"], 1)
        active_metrics = analyze_frame(cleaned)["metrics"]
        self.assertEqual(active_metrics["cleaned_records"], 0)

    def test_company_name_and_period_format_variants_are_flagged_not_merged(self):
        frame = pd.DataFrame({
            "Company Name": ["Acme Group", "ACME GROUP", "Acme Group Ltd"],
            "Financial Year": ["FY2024", "2024-25", "2024-25"],
            "Energy_kWh": [1, 2, 3],
        })
        report = analyze_frame(frame)
        issue_types = {issue["type"] for issue in report["column_issues"]}
        self.assertIn("company_name_variants", issue_types)
        self.assertIn("inconsistent_period_format", issue_types)
        cleaned, details, _ = apply_cleaning(frame)
        self.assertEqual(len(cleaned), 3)
        self.assertEqual(details["removed_records"], 0)
        self.assertEqual(cleaned.loc[1, "Company Name"], "ACME GROUP")

    def test_flags_invalid_dates_percentages_negative_values_and_units(self):
        frame = pd.DataFrame({
            "Financial Year": ["FY2024", "nonsense", "2024-25"],
            "Renewable_Energy_%": [50, 120, "unknown"],
            "Energy_kWh": [100, -1, "5 m3"],
        })
        report = analyze_frame(frame)
        self.assertEqual(report["metrics"]["invalid_records"], 2)
        types = {issue["type"] for issue in report["column_issues"]}
        self.assertIn("date_or_period", types)
        self.assertIn("percentage", types)
        self.assertIn("number_or_unit", types)


if __name__ == "__main__":
    unittest.main()
