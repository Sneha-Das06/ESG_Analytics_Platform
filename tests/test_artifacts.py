import tempfile
import unittest
from pathlib import Path

import pandas as pd

from src.artifacts import persist_immutable_artifact, validated_workbook_bytes, versioned_cleaning_paths
from src.data_quality import apply_cleaning


class CleaningArtifactTests(unittest.TestCase):
    def test_cleaning_preserves_original_and_creates_validated_new_version(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            original_frames = {
                "Energy_Data": pd.DataFrame({"Energy_kWh": [10, 10, "unknown"]}),
                "Water_Data": pd.DataFrame({"Water_m3": [4, None, 5]}),
            }
            original_bytes = validated_workbook_bytes(original_frames)
            original_path = Path("company/6/originals/original.xlsx")
            cleaned_path = Path("company/6/cleaned/run-unique.xlsx")
            persist_immutable_artifact(root, original_path, original_bytes)

            cleaned_frames = {}
            for name, frame in original_frames.items():
                cleaned, _, _ = apply_cleaning(frame, remove_exact_duplicates=(name == "Energy_Data"), confirmed=True)
                cleaned_frames[name] = cleaned
            cleaned_bytes = validated_workbook_bytes(cleaned_frames)
            persist_immutable_artifact(root, cleaned_path, cleaned_bytes)

            self.assertEqual((root / original_path).read_bytes(), original_bytes)
            self.assertNotEqual(cleaned_path, original_path)
            self.assertTrue((root / cleaned_path).is_file())
            with pd.ExcelFile(root / cleaned_path, engine="openpyxl") as workbook:
                self.assertEqual(workbook.sheet_names, ["Energy_Data", "Water_Data"])
                energy = pd.read_excel(workbook, sheet_name="Energy_Data")
                self.assertEqual(len(energy), 2)
                self.assertIn("unknown", energy["Energy_kWh"].tolist())

    def test_repeated_cleaning_of_same_filename_gets_unique_immutable_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            base = Path("data/uploads/12/34")
            original_a, cleaned_a = versioned_cleaning_paths(base, "a" * 64, "same workbook.xlsx", ".xlsx")
            original_b, cleaned_b = versioned_cleaning_paths(base, "a" * 64, "same workbook.xlsx", ".xlsx")
            self.assertEqual(original_a.parent, original_b.parent)
            self.assertEqual(cleaned_a.parent, cleaned_b.parent)
            self.assertEqual(len({original_a, original_b, cleaned_a, cleaned_b}), 4)

            persist_immutable_artifact(root, original_a, b"original bytes")
            persist_immutable_artifact(root, cleaned_a, b"cleaned version one")
            persist_immutable_artifact(root, original_b, b"original bytes")
            persist_immutable_artifact(root, cleaned_b, b"cleaned version two")

            self.assertEqual((root / original_a).read_bytes(), b"original bytes")
            self.assertEqual((root / original_b).read_bytes(), b"original bytes")
            self.assertEqual((root / cleaned_a).read_bytes(), b"cleaned version one")
            self.assertEqual((root / cleaned_b).read_bytes(), b"cleaned version two")
            with self.assertRaises(FileExistsError):
                persist_immutable_artifact(root, cleaned_a, b"must not overwrite")
            self.assertEqual((root / cleaned_a).read_bytes(), b"cleaned version one")

    def test_tenant_paths_remain_separate(self):
        company_a = versioned_cleaning_paths(Path("data/company_datasets/12"), "hash", "same.xlsx", ".xlsx")
        company_b = versioned_cleaning_paths(Path("data/company_datasets/13"), "hash", "same.xlsx", ".xlsx")
        department_a = versioned_cleaning_paths(Path("data/uploads/12/34"), "hash", "same.xlsx", ".xlsx")
        self.assertNotEqual(company_a[0], company_b[0])
        self.assertNotEqual(company_a[0], department_a[0])


if __name__ == "__main__":
    unittest.main()
