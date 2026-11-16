"""Immutable, uniquely versioned storage for cleaned upload artifacts."""

import hashlib
import os
import re
import uuid
from pathlib import Path


def versioned_cleaning_paths(base, source_hash, source_name, cleaned_extension):
    """Return unique relative original and cleaned paths for one cleaning run."""
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", Path(source_name).name) or "workbook"
    version_id = uuid.uuid4().hex
    stem = Path(safe_name).stem or "workbook"
    extension = cleaned_extension if str(cleaned_extension).startswith(".") else "." + str(cleaned_extension)
    prefix = f"{str(source_hash)[:16]}_{version_id}_"
    base = Path(base)
    original = base / "originals" / f"{prefix}{safe_name}"
    cleaned = base / "cleaned" / f"{prefix}{stem}_cleaned{extension}"
    return original, cleaned


def persist_immutable_artifact(root, relative_path, content):
    """Atomically create an artifact without replacing any existing path."""
    target = Path(root) / Path(relative_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    content_hash = hashlib.sha256(content).hexdigest()

    def existing_unchanged():
        if hashlib.sha256(target.read_bytes()).hexdigest() == content_hash:
            return target
        raise FileExistsError("A stored version already exists at this path and was left unchanged.")

    if target.exists():
        return existing_unchanged()

    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as temp_file:
            temp_file.write(content)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        try:
            # Creating a hard link is atomic and fails if another run claimed the target.
            os.link(temporary, target)
        except FileExistsError:
            return existing_unchanged()
        return target
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def validated_workbook_bytes(frames):
    """Serialize workbook sheets and reopen every sheet before it can be activated."""
    from io import BytesIO

    import pandas as pd

    items = list(frames.items())
    if not items:
        raise ValueError("A cleaned workbook must contain at least one sheet.")
    names = [str(name)[:31] for name, _ in items]
    if len(set(names)) != len(names):
        raise ValueError("Workbook sheet names must remain unique after Excel's 31-character limit.")

    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        for (name, frame), sheet_name in zip(items, names):
            frame.to_excel(writer, index=False, sheet_name=sheet_name)
    payload = output.getvalue()

    try:
        with pd.ExcelFile(BytesIO(payload), engine="openpyxl") as workbook:
            if workbook.sheet_names != names:
                raise ValueError("Generated workbook sheet names do not match the cleaning preview.")
            for (original_name, expected), sheet_name in zip(items, names):
                actual = pd.read_excel(workbook, sheet_name=sheet_name)
                if len(actual) != len(expected):
                    raise ValueError(f"Generated workbook row count changed unexpectedly in {original_name}.")
                if [str(column) for column in actual.columns] != [str(column) for column in expected.columns]:
                    raise ValueError(f"Generated workbook columns changed unexpectedly in {original_name}.")
    except Exception as exc:
        if isinstance(exc, ValueError) and str(exc).startswith("Generated workbook"):
            raise
        raise ValueError("Generated cleaned workbook could not be reopened and validated.") from exc
    return payload
