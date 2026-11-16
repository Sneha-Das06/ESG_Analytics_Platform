# ESG Analytics Platform — ESG Management System

This Streamlit application provides workbook-based ESG analytics, SQLite persistence, account roles, company permissions, manual ESG records, audit history, and downloadable reports. Workbook files are deployment data and are not included in the public source repository. Fresh installations can register companies and upload workbooks through the application; legacy imports require privately supplied registry and workbook files.

## Windows installation and startup

Use PowerShell from the project directory. The application uses SQLite schema version 5 and supports a local, single-instance deployment.

### Fresh installation

Create a virtual environment and install the declared dependencies:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

For a persistent database outside OneDrive, configure the path before initialization. You can set it for the current PowerShell session:

```powershell
$env:ESG_DATABASE_PATH = Join-Path $env:LOCALAPPDATA 'ESG_Analytics_Platform\data\esg_management.sqlite3'
New-Item -ItemType Directory -Force (Split-Path $env:ESG_DATABASE_PATH) | Out-Null
```

For future sessions, put the equivalent setting in the ignored project `.env` file:

```dotenv
ESG_DATABASE_PATH=%LOCALAPPDATA%\ESG_Analytics_Platform\data\esg_management.sqlite3
```

Initialize only an intentionally new database, create the first Platform Super Admin, then start Streamlit:

```powershell
python -m database init
python -m database create-platform-admin
streamlit run app.py
```

The admin setup prompts for a name, email, and password (minimum 12 characters) and refuses to create a second Platform Super Admin. After startup, companies can be registered through the existing **Register** flow by choosing **Company / Company Manager**. A fresh install does not need the legacy migration command or a companies registry file. The old `esg.db` demo file is not used by this setup.

If `ESG_DATABASE_PATH` is explicitly set but the file is missing, startup fails closed. Run `python -m database init` only when deliberately creating a new database at that path. **Do not use `init` to relocate or recover an existing database.** For an existing database, verify the path and restore/relocation plan first; keep the source and verified backup until cutover is approved.

### Environment and secrets

- `ESG_DATABASE_PATH` selects the SQLite file. If unset, the application uses `data/esg_management.sqlite3` in the project.
- `ESG_MFA_ENCRYPTION_KEY` is required for Platform Super Admin and Company Manager MFA. Store it in a protected secret store or a private, access-controlled environment file; never commit or log it.
- SMTP password-reset email is optional. Configure `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, and `SMTP_FROM` in the deployment secret store. Without SMTP, reset requests use the application's generic response and host-side recovery options.
- Login, MFA, and reset throttles can be tuned with the `ESG_LOGIN_*`, `ESG_MFA_*`, and `ESG_RESET_*` settings described in the authentication section below.

Do not put real credentials or encryption keys in README examples. The local `.env` file is ignored by Git and must remain private.

## Accounts and access

| Role | Access |
| --- | --- |
| Company Manager | Registers a company and becomes its manager. Manages that tenant's profile, departments, employees, workbook, ESG data, reports, and audit history. |
| Normal User | Requests access to one company and department. The Company Manager approves the request and grants or denies write permission. All reads and writes are department-scoped. |
| Viewer | Requests platform approval. Once approved, can view and export read-only ESG information across active companies. |
| Platform Super Admin | Created only by the secure setup command. Manages platform security, Viewer approvals, technical backups, migration history, and ownership setup for legacy tenants. It has no ESG record or workbook analytics access. |

The login page's existing **Register** form includes an **Account type** selector. Choosing **Company / Company Manager** reveals the company profile and manager fields: registered and legal names, registration number/CIN, industry, type and size, country/state/city/address/postal code, official contact details, website, optional company logo, reporting year, manager name, designation, official email, mobile number, optional username, password confirmation, and the required terms and accuracy declarations. The same form keeps the Normal User and Viewer registration paths, including their department/company and approval rules. Company and manager data are written together in one transaction to the active SQLite database (selected by `ESG_DATABASE_PATH`, or the project default `data/esg_management.sqlite3`); the manager becomes an approved Company Manager and can sign in immediately. Registration numbers, official company emails, manager emails, usernames, and company names are protected against duplicates. Logos are checked as PNG/JPEG/WebP, limited to 2 MB and 12 megapixels, re-encoded to remove embedded metadata, and stored in the company row. Managers can update their own registration profile from **Company Workspace → Company Profile**.

Public registration cannot create a Platform Super Admin. A company registrant becomes that company's manager. Employee registrations wait for the relevant Company Manager; Viewer registrations wait for the Platform Super Admin. Passwords use PBKDF2-HMAC-SHA256. Database APIs reload account roles and tenant membership for each operation; department/company foreign keys and triggers reject cross-company data links. Added registration columns are applied as additive SQLite migrations; existing companies and ESG records are retained.

The legacy companies imported before tenant ownership have no assigned managers. A user can request Normal User access to the appropriate company; the Platform Super Admin can assign that pending user as the manager of an unowned legacy company. Users cannot be moved from one tenant to another through this flow.

## Legacy migration and backups

The legacy migration command imports workbook data using a private registry file. It is not part of a fresh database installation.

Before migration, privately provide the operational `config/companies.json` file and every workbook at the relative path listed for it. The registry contains organization labels and dataset paths, so do not publish the operational file or workbooks. `config/companies.example.json` contains fictional names and placeholder paths; it is not loaded automatically and must not be used as the real migration registry. The current migration command expects the exact path `config/companies.json`.

Run the migration only when intentionally importing legacy workbook data into the selected database:

```powershell
python -m src.migrate_legacy
```

Before importing, the command stores a timestamped copy of the registry, the `data/raw` folder, and the active SQLite database under `data/backups/`. It validates each workbook independently, associates legacy sheet rows with companies and departments, and imports idempotently. Missing private files are reported as missing; migration does not create their contents. It does not infer fiscal years or convert workbook aggregates into year-specific manual ESG records. The existing workbooks are not edited by migration.
The migration report and Platform Database Management page show validated, partial, failed, or missing sources. Re-running the import does not duplicate previously imported rows.

Company Managers can upload a workbook for their own tenant; each upload is stored as a new file and the previous workbook is retained. Department employees may upload one CSV or one-sheet Excel file to their assigned department only. Company and department records are archived rather than permanently erased.

### Database backups and restore

A Platform Super Admin can use **Database Management → Create database backup**. The application uses SQLite's online backup API and stores backups in a `backups` directory beside the active database. The restore screen also discovers older backups in the project `data/backups` directory. Prefer this application workflow for routine backups.

For an independent Windows backup, use PowerShell and Python's SQLite online backup API. The example below creates a timestamped file beside the configured database, refuses to overwrite it, opens the source read-only, then checks database integrity, schema version, and foreign keys:

```powershell
$databasePath = if ($env:ESG_DATABASE_PATH) { $env:ESG_DATABASE_PATH } else { Join-Path $PWD 'data\esg_management.sqlite3' }
$backupDirectory = Join-Path (Split-Path $databasePath -Parent) 'backups'
New-Item -ItemType Directory -Force $backupDirectory | Out-Null
$backupPath = Join-Path $backupDirectory ('esg_backup_' + (Get-Date -Format 'yyyyMMdd_HHmmss') + '.sqlite3')
if (Test-Path -LiteralPath $backupPath) { throw 'Backup target already exists; choose a new name.' }

python -c "import sqlite3,sys; from pathlib import Path; source=Path(sys.argv[1]).resolve(); target=Path(sys.argv[2]).resolve(); assert source.is_file() and not target.exists(); src=sqlite3.connect(source.as_uri()+'?mode=ro',uri=True); dst=sqlite3.connect(str(target)); src.backup(dst); integrity=dst.execute('PRAGMA integrity_check').fetchone()[0]; version=dst.execute('PRAGMA user_version').fetchone()[0]; violations=list(dst.execute('PRAGMA foreign_key_check')); print('integrity_check='+integrity); print('schema_version='+str(version)); print('foreign_key_violations='+str(len(violations))); dst.close(); src.close(); raise SystemExit(0 if integrity=='ok' and not violations else 1)" $databasePath $backupPath
if ($LASTEXITCODE -ne 0) { throw 'Backup verification failed; retain both files and investigate.' }
```

Keep verified backups separately from the active database and protect them as sensitive data. Do not copy only the main SQLite file while the app is running. To restore, a Platform Super Admin uses **Database Management → Restore database**, selects a verified backup, and enters `RESTORE DATABASE`. The current database is backed up before restoration. Never manually replace the active database while the app is running. For database relocation, stop the application, verify a separate copy, and change the configured path only after approving and validating the cutover.

## Data quality review, cleaning, and restore

The **Data Quality** page analyzes workbook sheets available to the signed-in role and shows record counts, exact and configured business-key duplicate candidates, blank rows, partial missing values, invalid values, flagged rows, and a quality score. Company names, periods, dates, units, percentages, and numeric fields are checked; uncertain name/period variants are flagged for review and are never merged automatically. Whitespace-only normalization is offered as a safe correction, and the original upload is kept byte-for-byte.

Company Managers can run a cleaning preview from **Data Management** on their own company's workbook. Normal Users can preview and clean permitted uploads in their assigned department. Select any removal options and explicitly confirm them before applying; partial records are retained. Each cleaning run first makes and integrity-checks a full SQLite backup, preserves the uploaded source and creates a new immutable cleaned file, then records a tenant-scoped history entry. History includes the actor, company/department, time, and counts of changed, removed, skipped, and flagged records. Download the scoped quality report as Excel or PDF from **Data Quality**.

To restore a previous company workbook, use its entry in the cleaning history and select **Restore original dataset**; this switches the active dataset pointer and retains both files and the audit history. To restore a full database backup, a Platform Super Admin uses **Database Management → Restore database**, chooses a verified backup, and types `RESTORE DATABASE`. The current database is backed up before restoration. New database backups are stored beside the active database in its `backups` folder; legacy backups in project `data/backups/` remain supported. Cleaning backups are stored under the active database's `backups/cleaning/` folder. Never manually replace the active database while the application is running.

The SQLite database schema is currently version 5. Schema upgrades create a pre-upgrade SQLite backup before applying migrations. On an existing installation, do not initialize a different or empty database by mistake; confirm `ESG_DATABASE_PATH` first. Initialization does not relocate the database or re-import legacy workbooks.

## Manual records and reports

Use **Manual ESG Data** to enter company, department, financial year, energy (kWh), water (m³), carbon emissions (tCO₂e), waste (tonnes), renewable energy percentage, social score, employee information, and governance information. The form validates non-negative measures and percentages/scores from 0 to 100. A record is unique per company, department, and financial year. Reports include authorized workbook analysis, manual records, and department uploads in downloadable formats.

## Password reset email (optional)

The Forgot password form sends a single-use, 30-minute reset token only when SMTP is configured. Add these values to the local `.env` file (never commit it):

```text
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USER=your-smtp-user
SMTP_PASSWORD=your-smtp-password
SMTP_FROM=esg-platform@example.com
```

Without SMTP configuration, reset requests use a generic response. A Platform Super Admin can issue tokens to approved platform accounts, and a Company Manager can issue tokens only to approved employees in that company; share tokens through a trusted private channel. `ESG_USERNAME` and `ESG_PASSWORD` are no longer used for app login.

## Administrator authentication security

Platform Super Admin and Company Manager accounts must enroll an authenticator. The QR code is compatible with Google Authenticator and Microsoft Authenticator. Recovery codes are shown once; store them in a private password manager.

`ESG_MFA_ENCRYPTION_KEY` encrypts MFA secrets in the database. For a brand-new installation, generate one Fernet key and store it in the deployment secret store before the first administrator login:

```powershell
$env:ESG_MFA_ENCRYPTION_KEY = (python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
```

Do not generate a replacement key when using an existing or migrated database. The same key that encrypted its MFA secrets must be available to the application. If moving the app to another computer, provision that exact key before starting Streamlit. A different key cannot decrypt existing MFA secrets.

Back up the key separately from the database in an encrypted, access-controlled secret store or secure offline recovery store. Limit access to authorized operators and test that the key can be restored. Do not place it in source control, a report, a log, or an unprotected backup folder.

If the key is unavailable, first restore the exact key from its secure backup. If it is irrecoverably lost, an authorized host operator can perform offline MFA recovery using the Platform Super Admin password:

```powershell
python -m database reset-mfa
```

The command prompts for the Platform Super Admin email and password, the administrator account to reset, and the confirmation phrase `OFFLINE MFA RESET`. It clears that target account's MFA enrollment and sessions and records an audit event. Back up the database first. Then configure a new Fernet key in the secret store before the target next signs in and enrolls a new authenticator. This is a reset and re-enrollment process; it does not decrypt or recover the lost MFA secrets. Do not edit MFA database columns manually.

MFA can also be reset from the signed-in administrator's Security page after verifying the current password and an authenticator or unused recovery code. Login, MFA, and password-reset throttles are configurable with `ESG_LOGIN_MAX_ATTEMPTS`, `ESG_LOGIN_WINDOW_SECONDS`, `ESG_LOGIN_LOCKOUT_SECONDS`, `ESG_RESET_MAX_ATTEMPTS`, `ESG_RESET_WINDOW_SECONDS`, `ESG_RESET_LOCKOUT_SECONDS`, `ESG_MFA_MAX_ATTEMPTS`, `ESG_MFA_WINDOW_SECONDS`, and `ESG_MFA_LOCKOUT_SECONDS`. Defaults are five login or MFA failures per 15 minutes and three reset requests per hour, followed by a temporary lockout. Reset-token verification is throttled separately.

## Tests

Run the automated suite and compile the application from the activated virtual environment:

```powershell
python -m unittest discover -s tests -v
python -m compileall -q app.py database.py main.py config.py src tests
```

The automated tests use temporary databases and cover company registration, role and tenant authorization, department permissions, MFA and session security, rate limiting, database backup and restore, cleaning, and report exports. They do not require the production database.

For a manual registration check, start the app, choose **Register**, select **Company / Company Manager**, complete the form, then sign in with that account. Confirm the company appears in the manager's workspace. Repeat with a separate test company and verify the managers see only their own company. Use test data and a temporary database for manual testing; do not run these steps against production accounts or data.

The SQLite schema uses relational tables and SQL constraints, but the current implementation targets local single-instance SQLite. A multi-instance deployment requires a planned PostgreSQL migration and deployment-specific concurrency, backup, and recovery procedures. Keep credentials in a managed secret store.

### Database location and recovery

New backups are written to the active database's sibling `backups` directory. The restore screen checks both that directory and the legacy project `data/backups` directory. For recovery from a missing configured database, stop the app and verify the configured path and backup first; do not initialize an empty database over the recovery target. Restore through the application or a documented offline recovery procedure, verify SQLite integrity, then restart. Changing `ESG_DATABASE_PATH` does not copy or migrate the database, and the original database must be retained until a separately approved cutover is validated.
