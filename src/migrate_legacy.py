"""Back up and safely register the existing companies.json and Excel workbooks."""
import hashlib
import json
import math
import shutil
from datetime import datetime
from pathlib import Path

import database
from config import CONFIG_DIR
from src.data_loader import load_company_data
from src.validation import validate_company_data

ROOT = Path(__file__).resolve().parents[1]

def _json_value(value):
    try:
        if value is None or bool(__import__("pandas").isna(value)): return None
    except (TypeError, ValueError): pass
    if hasattr(value, "item"):
        try: value=value.item()
        except (ValueError, AttributeError): pass
    if isinstance(value,float) and not math.isfinite(value): return None
    if hasattr(value,"isoformat"): return value.isoformat()
    return value

def _import_workbook(company_id,relative,digest,frames,path=None):
    total=0
    department_ids={sheet:database.department_for_sheet(company_id,sheet,path) for sheet in frames}
    with database.connect(path) as con:
        for sheet,frame in frames.items():
            department_id=department_ids[sheet]
            batch=[]
            for row_number,(_,series) in enumerate(frame.iterrows(),start=2):
                payload={str(k):_json_value(v) for k,v in series.items()}
                batch.append((company_id,department_id,relative,digest,sheet,row_number,json.dumps(payload,ensure_ascii=False,allow_nan=False),database.now()))
                if len(batch)>=500:
                    cur=con.executemany('INSERT OR IGNORE INTO legacy_dataset_rows(company_id,department_id,dataset_path,source_hash,sheet_name,row_number,payload_json,imported_at) VALUES(?,?,?,?,?,?,?,?)',batch); total+=cur.rowcount; batch=[]
            if batch:
                cur=con.executemany('INSERT OR IGNORE INTO legacy_dataset_rows(company_id,department_id,dataset_path,source_hash,sheet_name,row_number,payload_json,imported_at) VALUES(?,?,?,?,?,?,?,?)',batch); total+=cur.rowcount
    return total

def migrate():
    registry = CONFIG_DIR / "companies.json"
    if not registry.is_file():
        raise FileNotFoundError(f"Company registry not found: {registry}")
    data = json.loads(registry.read_text(encoding="utf-8"))
    companies = data.get("companies", {})
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup = ROOT / "data" / "backups" / f"legacy_migration_{stamp}"
    backup.mkdir(parents=True, exist_ok=False)
    if database.DB_PATH.is_file():
        database.backup_database(backup / "existing_database.sqlite3")
    database.initialize_database(backup_existing=False)
    shutil.copy2(registry, backup / "companies.json")
    raw = ROOT / "data" / "raw"
    if raw.is_dir():
        shutil.copytree(raw, backup / "raw", dirs_exist_ok=True)
    outcomes=[]
    # Each source is immutable and imported only once; registration preserves any existing DB values.
    for name, relative in companies.items():
        source=(ROOT / relative).resolve()
        state="validated"; note="Company registered; workbook left unchanged."; frames=None
        if not source.is_file():
            state="missing"; note=f"Workbook is missing: {relative}"
        else:
            try:
                frames=load_company_data(source)
                issues=validate_company_data(frames)
                failures={sheet:detail for sheet,detail in issues.items() if detail}
                if failures:
                    state="partial"; note=f"Workbook loaded; validation found issues in {len(failures)} sheet(s)."
                else: note=f"Workbook loaded and validation passed ({len(frames)} sheets)."
            except Exception as exc:
                state="failed"; note=f"Workbook validation failed: {exc}"
        company_id=database.ensure_legacy_company(name,relative)
        digest=hashlib.sha256(source.read_bytes()).hexdigest() if source.is_file() else ""
        imported_rows=0
        if frames is not None:
            imported_rows=_import_workbook(company_id,relative,digest,frames)
            note += f" Imported {imported_rows} previously unseen workbook rows into SQLite."
        summary=f"{state}: {note} sha256={digest}"[:2000]
        with database.connect() as con:
            con.execute("INSERT INTO migration_log(source,status,message,created_at) VALUES(?,?,?,?) ON CONFLICT(source) DO UPDATE SET status=excluded.status,message=excluded.message,created_at=excluded.created_at",(relative,state,summary,database.now()))
        outcomes.append({"company":name,"status":state,"message":note,"rows_imported":imported_rows})
    report=backup / "migration_report.json"
    report.write_text(json.dumps({"backup":str(backup),"results":outcomes},indent=2),encoding="utf-8")
    return backup,outcomes

if __name__ == "__main__":
    location, results=migrate()
    print(f"Backup created: {location}")
    for row in results: print(f"{row['status'].upper():10} {row['company']}: {row['message']}")
