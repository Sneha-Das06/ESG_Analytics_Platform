# Render deployment guide

This guide describes a separate, single-instance synthetic-data demo. It is not a production migration procedure. Do not connect the service to a production database, use production accounts, or upload private company workbooks.

## What this configuration creates

- Python 3.12.16 via the root .python-version file.
- A Render Python web service named esg-analytics-demo, starting the Streamlit entry point app.py.
- A 5 GB persistent disk mounted at /opt/render/project/src/data.
- A required Render-managed secret prompt for ESG_MFA_ENCRYPTION_KEY.
- External CO2 loading disabled for this demo.

The Blueprint specifies Render's paid standard service plan because Render persistent disks require a paid web service. Review current plan and disk charges in Render before creating a service. No service is created and no charge is incurred by these files alone. Render documents that a persistent disk is only available to one service instance and that only files under its mount path persist:
https://render.com/docs/disks

## Why the disk mount covers this app

Render's native Python service source directory is /opt/render/project/src. The app's SQLite default is data/esg_management.sqlite3 under the project root. Database backups are stored beside that database, and company and department uploads are written under data/company_datasets/ and data/uploads/. The data/ mount therefore covers the current database, backups, and uploaded workbooks without changing application code.

The app also creates generated output directories under outputs/. Those are outside this mount and are not durable. The Streamlit report downloads are generated for download; do not treat outputs/ as durable company storage.

The Blueprint intentionally does not set ESG_DATABASE_PATH. With that variable unset, the application uses the default database inside the mounted data/ directory and the app initializes a new default database on first startup. Do not copy a production database to this disk. If you later set ESG_DATABASE_PATH, point it inside the mounted directory and explicitly initialize a new database at that path before starting the app. The application intentionally refuses to silently create a missing database when an explicit path is configured.

## Before creating the Render service

1. Confirm that the public GitHub repository contains only intended public source and documentation. Do not upload .env, database files, backups, Excel datasets, credentials, or production company information.
2. In Render, choose to create a service from the existing GitHub repository and select the main branch and the root render.yaml Blueprint.
3. Review the paid standard plan and 5 GB disk cost before confirming service creation. The disk is needed for persistent SQLite and upload storage.
4. Set the required ESG_MFA_ENCRYPTION_KEY secret before the service starts. Generate a new Fernet key exclusively for this demo in a private terminal, for example:

   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

   Paste the generated key directly into the Render secret field. Never put it in Git, a command line argument, application logs, or this guide. Store a protected recovery copy separate from Render. Do not reuse a key from another environment.
5. Leave SMTP variables unset for a demo unless a dedicated test mail account is configured. Never use production mail credentials or real user addresses.
6. Do not share the public service URL while completing the initial setup. Anyone who can reach the page can use the public registration flow to request or create accounts; company manager registration is approved immediately by the existing application behavior.

## One-time database and Platform Super Admin setup

The persistent disk must be attached at /opt/render/project/src/data before initializing the demo database. Never use, copy, or point this service at a production database.

### Recommended: keep the Blueprint default path

The Blueprint leaves ESG_DATABASE_PATH unset. On the first app startup, app.py initializes a new SQLite database at:

/opt/render/project/src/data/esg_management.sqlite3

This is the one-time initialization for the default path. Wait for the service to start successfully, then use the Render Shell attached to this running service to create the first administrator:

    cd /opt/render/project/src
    python -m database create-platform-admin

The command prompts interactively for the administrator's name, email, and password. Use a demo-only email address and a unique strong password. Do not pass a password as a shell argument or save it in a file. ESG_MFA_ENCRYPTION_KEY must already be configured so the administrator can complete MFA enrollment.

### If you need an explicit ESG_DATABASE_PATH

Initialize the target database on the mounted disk before setting ESG_DATABASE_PATH in the Render service settings. Choose the final path before creating demo accounts or records. Do not switch to a different database after demo data has been entered without a planned migration.

1. Deploy with ESG_DATABASE_PATH unset and confirm the service is running with the persistent disk mounted.
2. Open the Render Shell for that running service. For a new demo database at a custom path inside the mounted directory, run the one-time initialization command with the path set only for that command. Example:

       ESG_DATABASE_PATH=/opt/render/project/src/data/esg_demo.sqlite3 python -m database init

   Use the exact same path you intend to configure in Render. Do not run this command if the target file already exists with data. If you are keeping the default path, the first app startup has already initialized it and this command is unnecessary.
3. After the command succeeds, set ESG_DATABASE_PATH in the Render service environment to that exact initialized path and allow the service to restart.
4. After the restarted service is healthy, open its Render Shell and run the create-platform-admin command shown above. This command uses the service's configured ESG_DATABASE_PATH.

An explicitly configured path that does not exist causes the app to fail closed at startup. Therefore, never set the Render environment variable before the target database has been initialized on the mounted disk. Run database init only once for a new, intentionally empty demo database; do not put it in the start command or run it during normal restarts. Normal app startup checks the existing schema and does not replace a current database.

## Create four synthetic demo roles

Use only synthetic names, email addresses, company details, employee information, and ESG values.

1. **Platform Super Admin** — create this account once using the Render Shell command above. Complete the required MFA enrollment.
2. **Company Manager** — use the registration form and choose Company / Company Manager. Create a fictional demo company. The existing registration flow approves this manager immediately.
3. **Create a department first** — sign in as the Company Manager, open the Company Workspace, select the **Departments** tab, and add a department such as Environment using the New department field and Add department button.
4. **Normal User** — register for the same demo company and select the Environment department. The request remains pending until the Company Manager reviews it. The manager opens the **Employees** tab, selects the pending request, confirms the assigned department and edit/upload permission, then chooses **Approve employee**.
5. **Viewer** — register using the Viewer path. The request remains pending until the Platform Super Admin approves it from Platform Administration.

Verify each role's permitted pages and company/department scope. The Platform Super Admin manages platform administration and does not have ESG analytics data access. Keep the test company, users, and uploaded workbook synthetic; do not import production information.

## Verify persistence across restart

1. As the demo Company Manager, add a synthetic company profile and upload a synthetic workbook. If testing department uploads, use synthetic rows only.
2. Confirm the application can read the records and workbook after upload.
3. Restart the Render service from its dashboard.
4. Sign in again and confirm that the same demo accounts, database records, and uploaded files remain available.
5. Repeat after a controlled redeploy if needed, then confirm the database and upload paths are still under /opt/render/project/src/data.
6. Use the app's database backup feature for an application-level backup. A backup stored on the same persistent disk is not an independent disaster-recovery copy. Download or copy an encrypted backup to a separately controlled location using an approved process, and test recovery only with a disposable demo database.

## Operational limits and remaining checks

- This configuration runs one Streamlit service instance with SQLite. Do not scale it to multiple instances or use it as a multi-instance production database.
- The persistent disk is single-instance storage. Render documents that services with attached disks cannot scale to multiple instances and have deployment downtime during disk-backed redeploys: https://render.com/docs/disks
- The database and user-uploaded files contain sensitive information even in a demo. Restrict access to the Render account and keep all demo contents synthetic.
- The existing application uses local filesystem storage. A production multi-instance deployment requires an application-level migration to a shared database such as PostgreSQL and durable object storage; this guide does not make those changes.
- Dependency installation, startup, role workflow, and restart persistence have not been tested on Render. Validate them in the isolated demo before inviting users.
- Render Shell and persistent disks are paid-service features. Check current plan availability and billing before creating the service. Free services do not provide persistent disks or Shell access: https://render.com/docs/free