"""Non-destructive data quality analysis and explicitly confirmed cleaning.

All functions operate on copies. Source files and caller-owned DataFrames are never
modified. De-duplication uses exact row equality or explicitly selected business
columns; it never applies fuzzy name matching.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timezone
from io import BytesIO

import pandas as pd


PERCENT_RE = re.compile(r"(?:%|percent|percentage|_pct\b|pct\b)", re.I)
NUMERIC_RE = re.compile(
    r"energy|electric|diesel|fuel|water|carbon|co2|emission|waste|production|"
    r"purchase|amount|quantity|employee|headcount|turnover|salary|injur|incident|"
    r"hours|score|number|percent|percentage|_pct\b|"
    r"withdraw|consum|recycl|certif|supplier|material",
    re.I,
)
DATE_RE = re.compile(r"date|period|financial[\s_]*year|fiscal[\s_]*year|reporting[\s_]*year|^year$", re.I)
PERIOD_RE = re.compile(r"^(?:FY\s*)?(?:19|20|21)\d{2}(?:\s*[-/]\s*(?:FY\s*)?(?:19|20|21)?\d{2})?(?:\.0+)?$", re.I)
UNIT_RE = re.compile(r"\b(kwh|mwh|gj|mj|m3|m³|kg|tonnes?|tco2e|t\s*co2e)\b", re.I)


def _is_missing(value):
    try:
        return value is None or bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def normalize_whitespace(frame: pd.DataFrame):
    """Return a normalized copy plus row-level change flags."""
    normalized = frame.copy(deep=True)
    changed = pd.Series(False, index=frame.index, dtype=bool)
    for column in frame.columns:
        if not (pd.api.types.is_object_dtype(frame[column]) or pd.api.types.is_string_dtype(frame[column])):
            continue
        original = frame[column]

        def tidy(value):
            if _is_missing(value):
                return pd.NA
            return re.sub(r"\s+", " ", str(value), flags=re.UNICODE).strip() or pd.NA

        cleaned = original.map(tidy).astype("string")
        changed |= (original.astype("string").fillna("<NA>") != cleaned.fillna("<NA>"))
        normalized[column] = cleaned
    return normalized, changed


def _numeric_values(series):
    # Commas are accepted only as conventional thousands separators. Percent signs
    # are stripped for validation, but remain untouched in the original source.
    text = series.astype("string").str.strip().str.replace(",", "", regex=False)
    text = text.str.replace(r"\s*%\s*$", "", regex=True)
    text = text.str.replace(r"\s*(?:kwh|mwh|gj|mj|m3|m³|kg|tonnes?|tco2e|t co2e)\s*$", "", regex=True, case=False)
    return pd.to_numeric(text, errors="coerce")


def _unit_mismatches(series, column):
    label = str(column).casefold()
    expected = None
    if "energy" in label or "electric" in label or "diesel" in label or "fuel" in label:
        expected = {"kwh"} if "kwh" in label else {"mwh"} if "mwh" in label else {"gj"} if "gj" in label else {"mj"} if "mj" in label else None
    elif "water" in label or "withdraw" in label or "consum" in label:
        expected = {"m3", "m³"} if ("m3" in label or "m³" in label) else None
    elif "carbon" in label or "co2" in label or "emission" in label:
        expected = {"tco2e", "t co2e"} if ("tco2e" in label or "co2e" in label) else None
    elif "waste" in label or "material" in label or "quantity" in label:
        expected = {"tonne", "tonnes", "t"} if ("tonne" in label or "tonnes" in label) else {"kg"} if "kg" in label else None
    mismatch = pd.Series(False, index=series.index)
    if expected is None:
        return mismatch
    units = series.astype("string").str.extract(UNIT_RE, expand=False).str.casefold().str.replace(r"\s+", " ", regex=True)
    return units.notna() & ~units.isin(expected)


def _period_kind(value):
    if _is_missing(value):
        return None
    text = re.sub(r"\s+", "", str(value))
    if PERIOD_RE.fullmatch(text):
        return "fiscal_period" if ("-" in text or "/" in text or text.upper().startswith("FY")) else "year"
    parsed = pd.to_datetime(text, errors="coerce")
    return "date" if not pd.isna(parsed) else "invalid"


def _period_format(value):
    if _is_missing(value):
        return None
    text = re.sub(r"\s+", "", str(value))
    if not PERIOD_RE.fullmatch(text):
        return "date" if _period_kind(value) == "date" else "invalid"
    if text.upper().startswith("FY"):
        return "FY-prefixed year/range"
    if "/" in text:
        return "slash-separated year/range"
    if "-" in text:
        return "dash-separated year/range"
    return "calendar year"


def analyze_frame(frame: pd.DataFrame, business_keys=None, source_row_offset=2, preview_limit=100):
    """Analyze one upload without mutating it; cleaned_records counts rows needing safe whitespace normalization."""
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("Data quality analysis requires a pandas DataFrame.")
    source = frame.copy(deep=True)
    normalized, normalized_changed = normalize_whitespace(source)
    business_keys = list(dict.fromkeys(business_keys or []))
    unknown = [column for column in business_keys if column not in source.columns]
    if unknown:
        raise ValueError("Unknown business key column(s): " + ", ".join(map(str, unknown)))

    row_empty = normalized.isna().all(axis=1) if len(source.columns) else pd.Series(True, index=source.index)
    nonempty = ~row_empty
    complete_record = normalized.notna().all(axis=1) if len(source.columns) else pd.Series(False, index=source.index)
    # Partial ESG records remain in place for review even when their visible
    # columns happen to repeat; only fully populated duplicate rows are removable.
    exact_candidates = source.duplicated(keep="first") & nonempty
    exact_remove = exact_candidates & complete_record
    exact_group = source.duplicated(keep=False) & nonempty
    if business_keys:
        complete_keys = source[business_keys].notna().all(axis=1) & nonempty
        business_candidates = source.duplicated(subset=business_keys, keep="first") & complete_keys
        business_remove = business_candidates & complete_record
        business_group = source.duplicated(subset=business_keys, keep=False) & complete_keys
    else:
        business_candidates = pd.Series(False, index=source.index)
        business_remove = pd.Series(False, index=source.index)
        business_group = pd.Series(False, index=source.index)
    duplicate_remove = exact_remove | business_remove
    duplicate_candidates = exact_candidates | business_candidates

    missing_by_column = {}
    if len(source.columns):
        missing_by_column = {
            str(column): int(count)
            for column, count in normalized.loc[nonempty].isna().sum().items()
            if count
        }
    missing_rows = normalized.loc[nonempty].isna().any(axis=1) if nonempty.any() else pd.Series(False, index=source.index[nonempty])

    invalid_cells = pd.DataFrame(False, index=source.index, columns=source.columns)
    column_issues = []
    for column in source.columns:
        label = str(column)
        values = normalized[column]
        present = values.notna()
        if PERCENT_RE.search(label):
            numeric = _numeric_values(values)
            bad = present & (numeric.isna() | numeric.lt(0) | numeric.gt(100))
            invalid_cells.loc[:, column] |= bad
            if bad.any():
                column_issues.append({"column": label, "type": "percentage", "count": int(bad.sum()), "message": "Expected a numeric percentage from 0 to 100."})
        elif NUMERIC_RE.search(label) and not DATE_RE.search(label):
            numeric = _numeric_values(values)
            unit_bad = _unit_mismatches(values, label)
            bad = (present & numeric.isna()) | unit_bad
            if not re.search(r"score|percent|percentage|_pct\b", label, re.I):
                bad |= present & numeric.notna() & numeric.lt(0)
            invalid_cells.loc[:, column] |= bad
            if bad.any():
                column_issues.append({"column": label, "type": "number_or_unit", "count": int(bad.sum()), "message": "Contains a non-numeric, negative, or inconsistent-unit value."})
        if DATE_RE.search(label):
            normalized_text = values.astype("string").str.replace(r"\s+", "", regex=True)
            valid_year_period = normalized_text.str.fullmatch(PERIOD_RE, na=False)
            if label.casefold() in ("year", "financial_year", "fiscal_year", "reporting_year") or "year" in label.casefold():
                bad = present & ~valid_year_period
            else:
                parsed = pd.to_datetime(values, errors="coerce", format="mixed")
                bad = present & ~valid_year_period & parsed.isna()
            invalid_cells.loc[:, column] |= bad
            if bad.any():
                column_issues.append({"column": label, "type": "date_or_period", "count": int(bad.sum()), "message": "Contains an invalid date or reporting period."})

    invalid_rows = invalid_cells.any(axis=1) if len(source.columns) else pd.Series(False, index=source.index)
    company_name_variants = []
    format_review = pd.Series(False, index=source.index)
    for column in source.columns:
        if "company" not in str(column).casefold():
            continue
        values = source.loc[nonempty, column].dropna().astype(str)
        groups = defaultdict(set)
        for value in values.unique():
            canonical = re.sub(r"\s+", " ", value).strip().casefold()
            groups[canonical].add(value)
        company_name_variants = []
        for variants in groups.values():
            normalized_variants = {re.sub(r"\s+", " ", value).strip() for value in variants}
            if len(variants) > 1 and len(normalized_variants) > 1:
                company_name_variants.append(sorted(variants))
                format_review |= source[column].isin(variants)
        if company_name_variants:
            column_issues.append({"column": str(column), "type": "company_name_variants", "count": len(company_name_variants), "message": "Company-name capitalization variants need manual review; they are never merged automatically."})

    period_variants = {}
    for column in source.columns:
        if "year" not in str(column).casefold() and "period" not in str(column).casefold():
            continue
        values = normalized.loc[nonempty, column].dropna().astype(str)
        formats = sorted({(_period_format(value) or "missing") for value in values})
        if len(formats) > 1:
            period_variants[str(column)] = formats
            format_review |= normalized[column].notna()
            column_issues.append({"column": str(column), "type": "inconsistent_period_format", "count": int(normalized[column].notna().sum()), "message": "Reporting periods use multiple formats and need manual review; values are not rewritten."})

    valid_mask = nonempty & ~missing_rows.reindex(source.index, fill_value=False) & ~invalid_rows & ~format_review & ~duplicate_remove
    total = len(source)
    nonempty_count = int(nonempty.sum())
    metrics = {
        "total_records": total,
        "valid_records": int(valid_mask.sum()),
        "duplicate_records": int(duplicate_candidates.sum()),
        "exact_duplicate_records": int(exact_candidates.sum()),
        "business_key_duplicate_records": int(business_candidates.sum()),
        "removable_duplicate_records": int(duplicate_remove.sum()),
        "incomplete_duplicate_records": int((duplicate_candidates & ~complete_record).sum()),
        "empty_rows": int(row_empty.sum()),
        "missing_values": int(sum(missing_by_column.values())),
        "missing_by_column": missing_by_column,
        "invalid_records": int((invalid_rows & nonempty).sum()),
        "cleaned_records": int(normalized_changed.sum()),
        "quality_score": round(100.0 * int(valid_mask.sum()) / nonempty_count, 2) if nonempty_count else 100.0,
        "flagged_records": int(((missing_rows.reindex(source.index, fill_value=False)) | invalid_rows | format_review).sum()),
    }
    def row_numbers(mask):
        return [int(i) + source_row_offset for i, flagged in enumerate(mask.tolist()) if flagged][:preview_limit]

    preview_mask = exact_group | business_group
    duplicate_preview = source.loc[preview_mask].head(preview_limit).copy()
    empty_preview = source.loc[row_empty].head(preview_limit).copy()
    flagged_mask = (missing_rows.reindex(source.index, fill_value=False) | invalid_rows | format_review) & nonempty
    flagged_preview = source.loc[flagged_mask].head(preview_limit).copy()
    return {
        "metrics": metrics,
        "column_issues": column_issues,
        "company_name_variants": company_name_variants,
        "period_format_variants": period_variants,
        "row_numbers": {
            "empty": row_numbers(row_empty),
            "exact_duplicates": row_numbers(exact_remove),
            "business_key_duplicates": row_numbers(business_remove),
            "flagged": row_numbers(flagged_mask),
            "inconsistent_format": row_numbers(format_review),
            "normalized": row_numbers(normalized_changed),
        },
        "duplicate_preview": duplicate_preview,
        "empty_preview": empty_preview,
        "flagged_preview": flagged_preview,
        "normalized_frame": normalized,
        "original_frame": source,
        "normalized_changed_mask": normalized_changed,
    }


def apply_cleaning(frame: pd.DataFrame, *, remove_empty=False, remove_exact_duplicates=False,
                   business_keys=None, remove_business_key_duplicates=False, confirmed=False,
                   source_row_offset=2):
    """Build a cleaned copy; destructive options require explicit user confirmation."""
    destructive = bool(remove_empty or remove_exact_duplicates or (remove_business_key_duplicates and business_keys))
    if destructive and not confirmed:
        raise ValueError("Confirm the cleaning preview before removing any rows.")
    report = analyze_frame(frame, business_keys, source_row_offset)
    original = report["original_frame"]
    normalized = report["normalized_frame"]
    drop = pd.Series(False, index=original.index)
    skipped = pd.Series(False, index=original.index)
    if remove_empty:
        drop |= normalized.isna().all(axis=1) if len(normalized.columns) else True
    if remove_exact_duplicates:
        exact_candidates = original.duplicated(keep="first") & ~normalized.isna().all(axis=1)
        complete_rows = normalized.notna().all(axis=1)
        drop |= exact_candidates & complete_rows
        skipped |= exact_candidates & ~complete_rows
    if remove_business_key_duplicates and business_keys:
        keys = list(business_keys)
        complete_keys = original[keys].notna().all(axis=1) & ~normalized.isna().all(axis=1)
        key_candidates = original.duplicated(subset=keys, keep="first") & complete_keys
        complete_rows = normalized.notna().all(axis=1)
        drop |= key_candidates & complete_rows
        skipped |= key_candidates & ~complete_rows
    cleaned = normalized.loc[~drop].copy()
    changed_mask = report["normalized_changed_mask"] & ~drop
    skipped &= ~drop
    final = report["metrics"].copy()
    final.update({
        # Cleaned Records is the number of kept rows changed by safe whitespace
        # normalization. Confirmed removals and review skips remain separate counts.
        "cleaned_records": int(changed_mask.sum()),
        "removed_records": int(drop.sum()),
        "skipped_records": int(skipped.sum()),
        "changed_records": int(changed_mask.sum()),
        "cleaned_total_records": len(cleaned),
    })
    details = {
        **final,
        "removed_row_numbers": [int(i) + source_row_offset for i, flagged in enumerate(drop.tolist()) if flagged],
        "changed_row_numbers": [int(i) + source_row_offset for i, flagged in enumerate(changed_mask.tolist()) if flagged],
        "skipped_row_numbers": [int(i) + source_row_offset for i, flagged in enumerate(skipped.tolist()) if flagged],
        "business_key_columns": list(business_keys or []),
        "removed_empty_rows": int((drop & normalized.isna().all(axis=1)).sum()) if len(normalized.columns) else int(drop.sum()),
    }
    return cleaned, details, report


def aggregate_reports(reports):
    """Add per-sheet metrics without retaining DataFrames in the result."""
    keys = (
        "total_records", "valid_records", "duplicate_records", "exact_duplicate_records",
        "business_key_duplicate_records", "empty_rows", "missing_values", "invalid_records",
        "cleaned_records", "flagged_records",
    )
    totals = {key: sum(int(report["metrics"].get(key, 0)) for report in reports.values()) for key in keys}
    totals["missing_by_column"] = {}
    for report in reports.values():
        for column, count in report["metrics"].get("missing_by_column", {}).items():
            totals["missing_by_column"][column] = totals["missing_by_column"].get(column, 0) + int(count)
    nonempty = totals["total_records"] - totals["empty_rows"]
    totals["quality_score"] = round(100 * totals["valid_records"] / nonempty, 2) if nonempty else 100.0
    return totals


def excel_report_bytes(company, user, sheet_rows, history_rows=(), detail_frames=None, generated_at=None):
    """Create a tenant-scoped XLSX quality report with preview tabs."""
    generated_at = generated_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    metadata = pd.DataFrame([
        {"Company": company, "Generated by": user, "Generated at (UTC)": generated_at}
    ])
    summary = pd.DataFrame(list(sheet_rows))
    if not summary.empty and "missing_by_column" in summary:
        summary["missing_by_column"] = summary["missing_by_column"].map(lambda value: str(value))
    history = pd.DataFrame(list(history_rows))
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        metadata.to_excel(writer, index=False, sheet_name="Report Info")
        summary.to_excel(writer, index=False, sheet_name="Quality Summary")
        if not history.empty:
            history.to_excel(writer, index=False, sheet_name="Cleaning History")
        for index, (name, frame) in enumerate((detail_frames or {}).items(), start=1):
            safe = re.sub(r"[^A-Za-z0-9 ]+", "", str(name)).strip()[:20] or "Details"
            frame.to_excel(writer, index=False, sheet_name=f"{index} {safe}"[:31])
    return output.getvalue()


def pdf_report_bytes(company, user, sheet_rows, history_rows=(), generated_at=None):
    """Create a printable PDF summary with scope and audit counts."""
    from matplotlib.backends.backend_pdf import PdfPages
    import matplotlib.pyplot as plt

    generated_at = generated_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    output = BytesIO()
    with PdfPages(output) as pdf:
        figure, axis = plt.subplots(figsize=(11.7, 8.3))
        axis.axis("off")
        axis.text(0.02, 0.96, "ESG DATA QUALITY REPORT", fontsize=19, fontweight="bold", va="top")
        axis.text(0.02, 0.88, f"Company: {company}\nGenerated by: {user}\nGenerated at (UTC): {generated_at}", fontsize=10, va="top")
        frame = pd.DataFrame(list(sheet_rows))
        columns = [c for c in ("Dataset", "Sheet", "total_records", "valid_records", "duplicate_records", "empty_rows", "missing_values", "invalid_records", "cleaned_records", "quality_score") if c in frame.columns]
        if columns:
            table = axis.table(cellText=frame[columns].fillna("").values, colLabels=columns, loc="center", cellLoc="left")
            table.auto_set_font_size(False)
            table.set_fontsize(7)
            table.scale(1, 1.4)
        pdf.savefig(figure, bbox_inches="tight")
        plt.close(figure)
        histories = list(history_rows)
        if histories:
            history_frame = pd.DataFrame(histories)
            columns = [c for c in ("created_at", "company_name", "department_name", "actor_name", "operation", "changed_records", "removed_records", "skipped_records", "flagged_records") if c in history_frame.columns]
            figure, axis = plt.subplots(figsize=(11.7, 8.3))
            axis.axis("off")
            axis.set_title("Cleaning History", loc="left", fontsize=15, fontweight="bold", pad=18)
            table = axis.table(cellText=history_frame[columns].fillna("").values[:35], colLabels=columns, loc="center", cellLoc="left")
            table.auto_set_font_size(False)
            table.set_fontsize(7)
            table.scale(1, 1.3)
            pdf.savefig(figure, bbox_inches="tight")
            plt.close(figure)
    return output.getvalue()
