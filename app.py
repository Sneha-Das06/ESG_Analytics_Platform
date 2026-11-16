import json
import html
import re
import hashlib
import shutil
import os
import secrets
import smtplib
from email.message import EmailMessage
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st
import database

@st.cache_resource(show_spinner=False)
def _initialize_database_once(database_path):
    return database.initialize_database(database_path)

from config import (
    CONFIG_DIR,
    CO2_DATA_FILE,
    ENABLE_EXTERNAL_CO2,
)

from src.data_loader import (
    load_company_data,
    load_external_co2_data,
)

from src.validation import validate_company_data
from src import data_quality
from src.preprocessing import preprocess_company_data
from src.kpi_calculator import calculate_kpis
from src.kpi_dashboard import build_kpi_chart_data, style_kpi_figure
from src.report_generator import (
    company_report_excel_bytes, company_report_pdf_bytes, create_company_report,
    make_json_serializable, manual_records_excel_bytes, manual_records_pdf_bytes,
)
from src.artifacts import persist_immutable_artifact, validated_workbook_bytes, versioned_cleaning_paths
from src.analysis import analyze_company

from src.advanced_analysis import (
    perform_advanced_analysis,
    rank_companies,
)
from src.login_flow import authenticate_and_establish, authenticate_with_diagnostics


# ============================================================
# PAGE CONFIGURATION
# ============================================================

st.set_page_config(
    page_title="ESG Analytics Platform",
    page_icon="🌱",
    layout="wide",
    initial_sidebar_state="expanded",
)

_initialize_database_once(str(database.DB_PATH))


# ============================================================
# PROFESSIONAL CSS
# ============================================================

st.markdown(
    """
    <style>
    .stApp { background: #f5f8f7; }
    section[data-testid="stMain"] [data-testid="stMarkdownContainer"],
    section[data-testid="stMain"] [data-testid="stMarkdownContainer"] p,
    section[data-testid="stMain"] [data-testid="stMarkdownContainer"] li,
    section[data-testid="stMain"] [data-testid="stMarkdownContainer"] h1,
    section[data-testid="stMain"] [data-testid="stMarkdownContainer"] h2,
    section[data-testid="stMain"] [data-testid="stMarkdownContainer"] h3,
    section[data-testid="stMain"] [data-testid="stMarkdownContainer"] h4 {
        color: #0b1f33 !important;
    }
    .block-container { padding-top: 1.5rem; padding-bottom: 2rem; max-width: 1450px; }
    #MainMenu, footer { visibility: hidden; }
    header[data-testid="stHeader"] { visibility: visible !important; background: transparent; }
    button[data-testid="stExpandSidebarButton"] {
        display: inline-flex !important;
        visibility: visible !important;
        opacity: 1 !important;
        pointer-events: auto !important;
        align-items: center !important;
        justify-content: center !important;
        min-width: 42px !important;
        min-height: 42px !important;
        color: #ffffff !important;
        background: #0b1f33 !important;
        border: 1px solid #123c35 !important;
        border-radius: 8px !important;
    }
    button[data-testid="stExpandSidebarButton"]:hover { background: #123c35 !important; }
    button[data-testid="stExpandSidebarButton"] svg { color: #ffffff !important; fill: #ffffff !important; }
    button[data-testid="stSidebarCollapseButton"] { visibility: visible !important; opacity: 1 !important; pointer-events: auto !important; }

    .dashboard-header { background: linear-gradient(135deg, #0b1f33, #123c35); padding: 28px 32px; border-radius: 18px; margin-bottom: 25px; box-shadow: 0 8px 25px rgba(11, 31, 51, 0.15); }
    .dashboard-title { color: white; font-size: 34px; font-weight: 700; margin-bottom: 5px; }
    .dashboard-subtitle { color: #c8ddd7; font-size: 15px; }
    .metric-card { background: white; border-radius: 16px; padding: 22px; border: 1px solid #e3ebe8; box-shadow: 0 5px 18px rgba(11, 31, 51, 0.07); min-height: 125px; overflow-wrap: anywhere; }
    .metric-label { color: #71847f; font-size: 14px; font-weight: 500; }
    .metric-value { color: #123c35; font-size: 30px; font-weight: 700; margin-top: 8px; }
    .metric-description { color: #8a9a96; font-size: 12px; margin-top: 5px; }
    .section-title { color: #0b1f33; font-size: 23px; font-weight: 700; margin-top: 28px; margin-bottom: 15px; }
    div[data-testid="stExpander"] {
        background: #ffffff !important;
        border: 1px solid #cbded8 !important;
        border-radius: 12px !important;
        margin: 10px 0 !important;
    }
    div[data-testid="stExpander"] details > summary,
    div[data-testid="stExpander"] details > summary * {
        background: #e7f2ef !important;
        color: #0b1f33 !important;
        font-weight: 800 !important;
    }
    div[data-testid="stStatusWidget"],
    div[data-testid="stStatusWidget"] * {
        color: #0b1f33 !important;
        font-weight: 700 !important;
    }
    .login-logo { text-align: center; font-size: 55px; margin-bottom: 5px; }
    .login-title { text-align: center; color: #0b1f33; font-size: 36px; font-weight: 750; letter-spacing: 1px; }
    .login-subtitle { text-align: center; color: #657873; font-size: 16px; margin-bottom: 25px; }
    div[data-testid="stTextInput"] label, div[data-testid="stTextInput"] label p { color: #0b1f33 !important; font-weight: 700 !important; }
    .login-card { background: white; padding: 25px; border-radius: 20px; box-shadow: 0 12px 40px rgba(11, 31, 51, 0.12); border: 1px solid #e1eae7; }
    .welcome-title { text-align: center; color: #123c35; font-size: 27px; font-weight: 700; }
    .welcome-text { text-align: center; color: #7a8b86; margin-bottom: 25px; }
    .stButton > button { border-radius: 9px; min-height: 42px; background: #16856f; color: white; border: none; font-weight: 600; }
    .stButton > button:hover { background: #106a59; color: white; }
    .app-footer { text-align: center; color: #8a9a96; font-size: 12px; margin-top: 40px; padding: 20px; }
    /* Login tabs: improve contrast and use a restrained medium-bold weight. */
    section[data-testid="stMain"] [data-testid="stTabs"] button,
    section[data-testid="stMain"] [data-testid="stTabs"] button p {
        color: #334b5d !important;
        font-weight: 600 !important;
        opacity: 1 !important;
    }
    section[data-testid="stMain"] [data-testid="stTabs"] button[aria-selected="true"],
    section[data-testid="stMain"] [data-testid="stTabs"] button[aria-selected="true"] p {
        color: #0b1f33 !important;
        font-weight: 700 !important;
    }
    .stMarkdown p, .stMarkdown li {
        color: #0b1f33 !important;
        font-weight: 600 !important;
    }
    div[data-testid="stHeading"] h1,
    div[data-testid="stHeading"] h2,
    div[data-testid="stHeading"] h3,
    div[data-testid="stHeading"] h4 {
        color: #0b1f33 !important;
        font-weight: 800 !important;
    }
    div[data-testid="stAlert"],
    div[data-testid="stAlert"] * {
        color: #0b1f33 !important;
        font-weight: 700 !important;
    }
    /* KPI Analytics tabs and controls: scoped to this keyed tab widget. */
    .st-key-kpi_analytics_category_tabs button[role="tab"],
    .st-key-kpi_analytics_category_tabs button[data-baseweb="tab"],
    .st-key-kpi_analytics_category_tabs button[role="tab"] p,
    .st-key-kpi_analytics_category_tabs button[data-baseweb="tab"] p {
        color: #243f52 !important;
        background-color: #edf4f2 !important;
        font-weight: 700 !important;
        opacity: 1 !important;
    }
    .st-key-kpi_analytics_category_tabs button[role="tab"][aria-selected="true"],
    .st-key-kpi_analytics_category_tabs button[data-baseweb="tab"][aria-selected="true"],
    .st-key-kpi_analytics_category_tabs button[role="tab"][aria-selected="true"] p,
    .st-key-kpi_analytics_category_tabs button[data-baseweb="tab"][aria-selected="true"] p {
        color: #0b1f33 !important;
        background-color: #dceee8 !important;
        font-weight: 800 !important;
        opacity: 1 !important;
    }
    .st-key-kpi_analytics_category_tabs [data-testid="stMetricLabel"],
    .st-key-kpi_analytics_category_tabs [data-testid="stMetricLabel"] *,
    .st-key-kpi_analytics_category_tabs [data-testid="stMetricValue"],
    .st-key-kpi_analytics_category_tabs [data-testid="stMetricValue"] *,
    .st-key-kpi_analytics_category_tabs [data-testid="stCaptionContainer"],
    .st-key-kpi_analytics_category_tabs [data-testid="stCaptionContainer"] * {
        color: #0b1f33 !important;
        font-weight: 700 !important;
    }
    .kpi-summary-card .metric-label { color: #243f52 !important; font-weight: 700 !important; }
    .kpi-summary-card .metric-value { color: #0b1f33 !important; font-weight: 800 !important; }
    .kpi-summary-card .metric-description { color: #42586a !important; font-weight: 600 !important; }
    .kpi-analytics-page-title { color: #0b1f33 !important; font-weight: 800 !important; }
    .kpi-category-heading { color: #0b1f33 !important; font-size: 1.15rem; font-weight: 800 !important; margin: 0 0 0.5rem; }
    .st-key-kpi_company_filter [data-testid="stWidgetLabel"],
    .st-key-kpi_company_filter [data-testid="stWidgetLabel"] * {
        color: #0b1f33 !important;
        font-weight: 700 !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# ============================================================
# SESSION STATE
# ============================================================

if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

if st.session_state.authenticated:
    st.markdown(
        """
        <style>
        section[data-testid="stSidebar"] {
            background: #0b1f33 !important;
            visibility: visible !important;
            opacity: 1 !important;
        }
        section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"],
        section[data-testid="stSidebar"] label,
        section[data-testid="stSidebar"] p,
        section[data-testid="stSidebar"] span { color: white; }
        </style>
        """,
        unsafe_allow_html=True,
    )


# ============================================================
# LOGIN
# ============================================================

def _auth_client_ip():
    client_ip = getattr(st.context, "ip_address", None)
    if not client_ip:
        client_ip = st.session_state.setdefault("auth_rate_client", secrets.token_urlsafe(24))
    return str(client_ip)


def login_user(email, password):
    return authenticate_with_diagnostics(
        email, password,
        lambda submitted_email, submitted_password: database.authenticate(
            submitted_email, submitted_password,
            mfa_code=st.session_state.get("login_mfa_code", ""), client_ip=_auth_client_ip(),
        ),
        database.DB_PATH,
    )


# ============================================================
# LOAD COMPANY CONFIGURATION
# ============================================================

def load_company_config(company_names=None):
    with database.connect() as conn:
        rows = conn.execute("SELECT name,dataset_path FROM companies WHERE active=1 AND dataset_path IS NOT NULL AND dataset_path<>'' ORDER BY name").fetchall()
    db_companies = {row["name"]: row["dataset_path"] for row in rows}
    if company_names is not None:
        allowed = set(company_names)
        db_companies = {name: path for name, path in db_companies.items() if name in allowed}
    if db_companies or company_names is not None:
        return db_companies
    config_path = CONFIG_DIR / "companies.json"

    with open(
        config_path,
        "r",
        encoding="utf-8",
    ) as file:
        configuration = json.load(file)

    return configuration.get("companies", {})


# ============================================================
# PROCESS ALL COMPANIES
# ============================================================

def _company_source_signature(company_names=None):
    """Include source file versions in the outer analytics cache key."""
    companies = load_company_config(company_names)
    sources = [(name, path) for name, path in sorted(companies.items())]
    if ENABLE_EXTERNAL_CO2:
        sources.append(("__external_co2__", str(CO2_DATA_FILE)))
    signature = []
    for name, relative_path in sources:
        file_path = Path(relative_path) if name == "__external_co2__" else CONFIG_DIR.parent / relative_path
        try:
            stat = file_path.stat()
            signature.append((name, str(file_path.resolve()), stat.st_mtime_ns, stat.st_size))
        except OSError:
            signature.append((name, str(file_path), None, None))
    return tuple(signature)


@st.cache_data(show_spinner=False, ttl=300, max_entries=64)
def _load_company_workbook_cached(file_path, modified_ns, file_size):
    """Share parsed sheets between analytics and quality views until the file changes."""
    return load_company_data(file_path)


@st.cache_data(show_spinner=False, ttl=300, max_entries=32)
def load_esg_data(company_names=None, source_signature=None):

    companies = load_company_config(company_names)

    external_co2_data = None
    all_results = []
    validation_data = {}

    # Load external CO2 dataset

    if ENABLE_EXTERNAL_CO2 and CO2_DATA_FILE.exists():
        try:
            external_co2_data = load_external_co2_data(
                CO2_DATA_FILE
            )
        except Exception as error:
            validation_data["External CO2 Dataset"] = {
                "Application Error": [str(error)]
            }

    # Process each company

    for company_name, relative_path in companies.items():

        file_path = CONFIG_DIR.parent / relative_path

        if not file_path.exists():
            validation_data[company_name] = {
                "File Status": [
                    f"Data file not found: {relative_path}"
                ]
            }
            continue

        try:

            # Load data

            file_stat = file_path.stat()
            company_data = _load_company_workbook_cached(
                str(file_path), file_stat.st_mtime_ns, file_stat.st_size
            )

            # Validate data

            validation_results = validate_company_data(
                company_data
            )

            validation_data[company_name] = (
                validation_results
            )

            # Preprocess data

            processed_data = preprocess_company_data(
                company_data
            )

            # Calculate KPIs

            kpis = calculate_kpis(processed_data)

            # Basic analysis

            analysis_results = analyze_company(
                company_name,
                kpis,
                external_co2_data,
            )

            # Advanced analysis

            advanced_analysis = perform_advanced_analysis(
                company_name,
                kpis,
            )

            # Store results

            result = {
                "company": company_name,
                "kpis": kpis,
                "validation": validation_results,
                "kpi_chart_data": build_kpi_chart_data(processed_data),
                "analysis": analysis_results,
                "advanced_analysis": advanced_analysis,
            }

            all_results.append(result)

        except Exception as error:

            validation_data[company_name] = {
                "Application Error": [str(error)]
            }

    # Rank companies

    ranking = []

    if all_results:
        try:
            ranking = rank_companies(all_results)
        except Exception as error:
            validation_data["Ranking"] = {
                "Application Error": [str(error)]
            }

    return all_results, ranking, validation_data


# ============================================================
# LOGIN PAGE
# ============================================================

def show_login_page():
    st.markdown('<div class="login-logo">🌱</div>', unsafe_allow_html=True)
    st.markdown('<div class="login-title">ESG ANALYTICS PLATFORM</div>', unsafe_allow_html=True)
    st.markdown('<div class="login-subtitle">Sustainable Intelligence for Better Business Decisions</div>', unsafe_allow_html=True)
    _, center, _ = st.columns([1, 1.4, 1])
    with center:
        st.markdown('<div class="login-card"><div class="welcome-title">Welcome Back</div><div class="welcome-text">Sign in to your ESG workspace</div></div>', unsafe_allow_html=True)
        login_tab, registration_tab, reset_tab = st.tabs(["Login", "Register", "Forgot password"])
        with login_tab:
            email = st.text_input("Email", key="login_email")
            password = st.text_input("Password", type="password", key="login_password")
            mfa_code = st.text_input("Authenticator or recovery code (Managers and Super Admins)", key="login_mfa_code", max_chars=32)
            if st.button("LOGIN", use_container_width=True):
                if authenticate_and_establish(st.session_state, email, password, login_user):
                    # Start each new session on the first page for that role
                    # (Company Manager Dashboard for managers).
                    st.rerun()
                else:
                    st.error("Unable to log in. Check your email, password, and account approval status.")
        with registration_tab:
            account_type = st.selectbox(
                "Account type",
                ["Company / Company Manager", "Normal User (Department Employee)", "Viewer"],
                key="registration_account_type",
            )
            is_company_registration = account_type == "Company / Company Manager"
            is_employee_registration = account_type == "Normal User (Department Employee)"
            if is_company_registration:
                st.caption("Register a company and become its Company Manager. Fields marked * are required.")
            elif is_employee_registration:
                st.caption("Normal User access is limited to the selected company and department and requires its manager's approval.")
            else:
                st.caption("Viewer access is read-only and requires Platform approval.")
            show_registration_password = st.checkbox("Show registration password", key="show_registration_password") if is_company_registration else False
            companies = database.public_company_list() if is_employee_registration else []
            selected_company = None
            departments = []
            if is_employee_registration:
                if companies:
                    selected_company = st.selectbox("Company", companies, format_func=lambda x: x["name"], key="registration_company")
                    departments = database.list_public_departments(selected_company["id"])
                    if departments:
                        selected_department = st.selectbox("Department", departments, format_func=lambda x: x["name"], key="registration_department")
                    else:
                        selected_department = None
                        st.info("This company has no active departments available for requests.")
                else:
                    selected_department = None
                    st.info("No companies are registered yet.")

            with st.form("existing_account_registration_form"):
                if is_company_registration:
                    st.markdown("#### Company information")
                    col1, col2 = st.columns(2)
                    company_name = col1.text_input("Company name *")
                    legal_name = col2.text_input("Legal company name")
                    registration_number = col1.text_input("Registration number / CIN")
                    industry = col2.text_input("Industry / business sector")
                    company_type = col1.text_input("Company type")
                    company_size = col2.selectbox("Company size *", ["Choose size", "Small", "Medium", "Large"])
                    country = col1.text_input("Country *")
                    state = col2.text_input("State / region")
                    city = col1.text_input("City")
                    postal_code = col2.text_input("PIN / ZIP code")
                    registered_address = st.text_area("Complete registered address")
                    col1, col2 = st.columns(2)
                    official_email = col1.text_input("Official company email *")
                    official_phone = col2.text_input("Official phone number")
                    website = st.text_input("Company website")
                    financial_reporting_year = st.text_input("Financial reporting year", placeholder="2025 or 2024-2025")
                    logo_file = st.file_uploader("Company logo (optional; PNG, JPEG, or WebP; max 2 MB)", type=["png", "jpg", "jpeg", "webp"])
                    if logo_file is not None:
                        st.image(logo_file, caption="Logo preview", width=140)
                    st.markdown("#### Company Manager information")
                    col1, col2 = st.columns(2)
                    manager_name = col1.text_input("Manager full name *")
                    designation = col2.text_input("Designation")
                    manager_email = col1.text_input("Manager official email *")
                    mobile_number = col2.text_input("Mobile number")
                    username = st.text_input("Username (optional; email remains the login ID)")
                    col1, col2 = st.columns(2)
                    manager_password = col1.text_input("Password (at least 12 characters) *", type="default" if show_registration_password else "password")
                    confirm_password = col2.text_input("Confirm password *", type="default" if show_registration_password else "password")
                    terms_accepted = st.checkbox("I accept the Terms and Conditions. *")
                    declaration_accepted = st.checkbox("I declare that the company and manager information submitted is accurate. *")
                else:
                    st.markdown("#### Account information")
                    full_name = st.text_input("Full name *", key="registration_full_name")
                    account_email = st.text_input("Email *", key="registration_email")
                    account_password = st.text_input("Password (at least 12 characters) *", type="password", key="registration_password")
                submitted = st.form_submit_button("Register")

            if submitted:
                try:
                    if is_company_registration:
                        if not company_name.strip() or not country.strip() or not official_email.strip() or not manager_name.strip() or not manager_email.strip():
                            raise ValueError("Complete all required fields marked with *.")
                        if manager_password != confirm_password:
                            raise ValueError("The password and confirmation do not match.")
                        if company_size == "Choose size":
                            raise ValueError("Choose the company size.")
                        if logo_file is not None:
                            try:
                                from PIL import Image
                                from io import BytesIO
                                raw_logo = logo_file.getvalue()
                                if len(raw_logo) > 2 * 1024 * 1024:
                                    raise ValueError("Company logo must be no larger than 2 MB.")
                                with Image.open(BytesIO(raw_logo)) as source_logo:
                                    source_logo.verify()
                                with Image.open(BytesIO(raw_logo)) as source_logo:
                                    if source_logo.format not in ("PNG", "JPEG", "WEBP"):
                                        raise ValueError("Upload a valid PNG, JPEG, or WebP logo.")
                                    if source_logo.width > 6000 or source_logo.height > 6000 or source_logo.width * source_logo.height > 12_000_000:
                                        raise ValueError("Logo dimensions are too large. Use an image under 12 megapixels.")
                                    sanitized_logo = source_logo.convert("RGBA" if source_logo.mode in ("RGBA", "LA") else "RGB")
                                    sanitized_buffer = BytesIO()
                                    sanitized_logo.save(sanitized_buffer, format="PNG", optimize=True)
                                    logo_blob = sanitized_buffer.getvalue()
                                if len(logo_blob) > 2 * 1024 * 1024:
                                    raise ValueError("Sanitized logo exceeds 2 MB. Choose a smaller image.")
                            except ValueError:
                                raise
                            except Exception as image_error:
                                raise ValueError("The uploaded logo is not a valid image.") from image_error
                        else:
                            logo_blob = None
                        with st.spinner("Saving company and manager securely..."):
                            database.register_company(
                                company_name, manager_name, manager_email, manager_password, industry, website,
                                legal_name=legal_name, registration_number=registration_number, company_type=company_type,
                                company_size=company_size, country=country, state=state, city=city,
                                registered_address=registered_address, postal_code=postal_code,
                                official_email=official_email, official_phone=official_phone, logo_blob=logo_blob,
                                financial_reporting_year=financial_reporting_year, designation=designation,
                                mobile_number=mobile_number, username=username,
                                terms_accepted=terms_accepted, declaration_accepted=declaration_accepted,
                            )
                        st.success("Company registered. Your Company Manager account is ready; sign in using your manager email and password.")
                    elif is_employee_registration:
                        if not companies or selected_company is None or selected_department is None:
                            raise ValueError("Select an active company and department before requesting access.")
                        database.register_user(full_name, account_email, account_password, "normal_user", selected_company["id"], selected_department["id"])
                        st.success("Employee access request submitted. It will remain pending until the Company Manager approves it.")
                    else:
                        database.register_user(full_name, account_email, account_password, "viewer")
                        st.success("Viewer request submitted. It will remain pending until Platform approval.")
                except Exception as exc:
                    st.error(str(exc))
        with reset_tab:
            reset_email = st.text_input("Account email", key="reset_email")
            if st.button("Send reset link"):
                token = database.request_password_reset(reset_email, client_ip=_auth_client_ip())
                if token and os.getenv("SMTP_HOST") and os.getenv("SMTP_USER") and os.getenv("SMTP_PASSWORD"):
                    try:
                        message = EmailMessage()
                        message["Subject"] = "ESG platform password reset"
                        message["From"] = os.getenv("SMTP_FROM", os.getenv("SMTP_USER"))
                        message["To"] = reset_email
                        message.set_content(f"Your reset token is {token}. It expires in 30 minutes.")
                        with smtplib.SMTP(os.getenv("SMTP_HOST"), int(os.getenv("SMTP_PORT", "587")), timeout=20) as server:
                            server.starttls()
                            server.login(os.getenv("SMTP_USER"), os.getenv("SMTP_PASSWORD"))
                            server.send_message(message)
                    except Exception:
                        pass
                st.success("If an approved account exists and email is configured, reset instructions have been sent.")
            with st.form("password_reset_form"):
                reset_address = st.text_input("Email", key="apply_reset_email")
                reset_token = st.text_input("Reset token", key="apply_reset_token")
                new_password = st.text_input("New password (at least 12 characters)", type="password", key="apply_reset_password")
                submitted = st.form_submit_button("Reset password")
            if submitted:
                try:
                    database.reset_password(reset_address, reset_token, new_password)
                    st.success("Password updated. You can now log in.")
                except Exception as exc:
                    st.error(str(exc))
    st.markdown('<div class="app-footer">ESG Analytics Platform • Sustainability Intelligence</div>', unsafe_allow_html=True)


# ============================================================
# SIDEBAR
# ============================================================

def show_sidebar():
    user = st.session_state.user

    st.sidebar.markdown(
        """
        <div style="text-align:center; padding:10px 0 25px 0;">
            <div style="font-size:42px;">🌱</div>
            <div style="font-size:20px; font-weight:700;">
                ESG Analytics
            </div>
            <div style="font-size:12px;">
                Sustainability Intelligence
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.sidebar.markdown("---")

    pages = [
            "🏠 Dashboard", "🏢 Company Analysis", "📊 ESG Comparison",
            "📈 KPI Analytics", "⚠️ Data Quality", "📄 Reports",
            "📝 Manual ESG Data",
        ]
    if user["role"] == "super_admin":
        pages += ["🏭 Company Management", "👥 User Management", "🗄️ Database Management", "📤 Dataset Management"]
    page = st.sidebar.radio(
        "NAVIGATION",
        pages,
    )

    st.sidebar.markdown("---")

    st.sidebar.markdown(
        """
        <div style="font-size:12px; line-height:1.7;">
            <b>System</b><br>
            ESG Analytics Platform<br>
            Python + Streamlit<br>
            ESG Analytics Engine
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.sidebar.markdown("---")

    if st.sidebar.button(
        "Logout",
        use_container_width=True,
    ):
        st.session_state.authenticated = False
        st.session_state.pop("user", None)
        st.rerun()

    return page


# Tenant-aware navigation: role-specific pages are paired with DB-scoped queries below.
def show_sidebar():
    user = st.session_state.user
    role = user["role"]
    st.sidebar.markdown("<div style='text-align:center;padding:10px 0 25px'><div style='font-size:42px'>🌱</div><b style='font-size:20px'>ESG Analytics</b><br><span style='font-size:12px'>Sustainability Intelligence</span></div>", unsafe_allow_html=True)
    st.sidebar.markdown("---")
    if role == "platform_super_admin":
        pages = ["🛡️ Platform Administration", "🗄️ Database Management", "🔐 Security"]
        scope_label = "Platform administration"
    elif role == "normal_user":
        pages = ["🧑‍🔧 My Department", "⚠️ Data Quality", "📄 Reports"]
        scope_label = f"Department ID: {user.get('department_id') or 'pending'}"
    elif role == "company_manager":
        pages = ["🏠 Dashboard", "🏢 Company Analysis", "📊 ESG Comparison", "📈 KPI Analytics", "⚠️ Data Quality", "📄 Reports", "📝 Manual ESG Data", "🏭 Company Workspace", "📤 Dataset Management", "🔐 Security"]
        scope_label = "Company Manager"
    else:
        pages = ["🏠 Dashboard", "🏢 Company Analysis", "📊 ESG Comparison", "📈 KPI Analytics", "⚠️ Data Quality", "📄 Reports", "📝 Manual ESG Data"]
        scope_label = "Read-only Viewer • All companies"
    page = st.sidebar.radio("NAVIGATION", pages, key="main_nav")
    st.sidebar.markdown("---")
    st.sidebar.markdown(f"**{scope_label}**")
    st.sidebar.markdown("ESG Analytics Platform<br>Python + Streamlit<br>ESG Intelligence", unsafe_allow_html=True)
    st.sidebar.markdown("---")
    if st.sidebar.button("Logout", use_container_width=True):
        try:
            database.revoke_session(user)
        except Exception:
            st.error("Server-side logout could not be confirmed. Your session will also expire automatically.")
        st.session_state.authenticated = False
        st.session_state.pop("user", None)
        st.rerun()
    return page


# ============================================================
# DASHBOARD HEADER
# ============================================================

def show_header():

    st.markdown(
        """
<div class="dashboard-header">
    <div class="dashboard-title">
        ESG Analytics Platform
    </div>
    <div class="dashboard-subtitle">
        Environmental • Social • Governance
        Performance Intelligence
    </div>
</div>
        """,
        unsafe_allow_html=True,
    )


# ============================================================
# ESG SCORE
# ============================================================

def get_esg_score(result):

    advanced = result.get("advanced_analysis") or {}

    try:
        score = float(advanced.get("esg_score", 0))

        if pd.isna(score):
            return 0.0

        return score

    except (TypeError, ValueError):
        return 0.0


# ============================================================
# SUSTAINABILITY STATUS
# ============================================================

def get_status(result):

    advanced = result.get("advanced_analysis") or {}

    return advanced.get(
        "sustainability_status",
        "Unknown",
    )


# ============================================================
# DASHBOARD PAGE
# ============================================================

def show_dashboard(all_results, ranking):

    show_header()

    if not all_results:
        st.warning("No company data available.")
        return

    scores = [
        get_esg_score(result)
        for result in all_results
    ]

    average_score = sum(scores) / len(scores)

    top_company = max(
        all_results,
        key=get_esg_score,
    )

    total_companies = len(all_results)

    # External CO2 dataset row count

    co2_rows = 0

    if ENABLE_EXTERNAL_CO2 and CO2_DATA_FILE.exists():
        try:
            co2_data = load_external_co2_data(
                CO2_DATA_FILE
            )
            co2_rows = len(co2_data)
        except Exception:
            co2_rows = 0

    st.markdown(
        '<div class="section-title">ESG PERFORMANCE OVERVIEW</div>',
        unsafe_allow_html=True,
    )

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.markdown(
            f"""
            <div class="metric-card">
                <div class="metric-label">Average ESG Score</div>
                <div class="metric-value">{average_score:.2f}</div>
                <div class="metric-description">
                    Across processed companies
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with col2:
        st.markdown(
            f"""
            <div class="metric-card">
                <div class="metric-label">Companies Analyzed</div>
                <div class="metric-value">{total_companies}</div>
                <div class="metric-description">
                    Successfully processed datasets
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with col3:
        st.markdown(
            f"""
            <div class="metric-card">
                <div class="metric-label">Top Performer</div>
                <div class="metric-value" style="font-size:22px;">
                    {top_company["company"]}
                </div>
                <div class="metric-description">
                    ESG Score: {get_esg_score(top_company):.2f}
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with col4:
        st.markdown(
            f"""
            <div class="metric-card">
                <div class="metric-label">External CO₂ Dataset</div>
                <div class="metric-value" style="font-size:25px;">
                    {co2_rows:,}
                </div>
                <div class="metric-description">
                    Available data rows
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    # ESG score chart

    st.markdown(
        '<div class="section-title">ESG SCORE COMPARISON</div>',
        unsafe_allow_html=True,
    )

    chart_data = pd.DataFrame({
        "Company": [
            result["company"]
            for result in all_results
        ],
        "ESG Score": scores,
    })

    chart_data = chart_data.sort_values(
        "ESG Score",
        ascending=True,
    )

    fig = px.bar(
        chart_data,
        x="ESG Score",
        y="Company",
        orientation="h",
        text="ESG Score",
        title="Company ESG Performance",
    )

    fig.update_traces(
        texttemplate="<b>%{text:.2f}</b>",
        textposition="outside",
        textfont=dict(size=14, color="#0b1f33"),
    )

    fig.update_layout(
        height=max(430, 55 * len(chart_data) + 110),
        autosize=True,
        paper_bgcolor="white",
        plot_bgcolor="white",
        font=dict(color="#0b1f33", size=14, family="Arial, sans-serif"),
        title=dict(
            text="<b>Company ESG Performance</b>",
            font=dict(size=20, color="#0b1f33", family="Arial, sans-serif"),
            x=0.01,
            xanchor="left",
        ),
        margin=dict(l=30, r=85, t=65, b=45),
        xaxis=dict(title=dict(text="ESG Score", font=dict(size=15, color="#0b1f33")), tickfont=dict(size=13, color="#0b1f33"), automargin=True),
        yaxis=dict(title=dict(text="Company", font=dict(size=15, color="#0b1f33")), tickfont=dict(size=14, color="#0b1f33"), automargin=True),
    )

    st.plotly_chart(
        fig,
        use_container_width=True,
        config={"responsive": True},
    )

    # Ranking table

    st.markdown(
        '<div class="section-title">COMPANY RANKING</div>',
        unsafe_allow_html=True,
    )

    if ranking:

        ranking_table = pd.DataFrame([
            {
                "Rank": item.get("rank", "-"),
                "Company": item.get("company", "-"),
                "ESG Score": item.get("esg_score", 0),
            }
            for item in ranking
        ])

        st.dataframe(
            ranking_table,
            use_container_width=True,
            hide_index=True,
        )

    else:
        st.info("Ranking data is not available.")


# ============================================================
# COMPANY ANALYSIS
# ============================================================

def show_company_analysis(all_results):

    show_header()

    if not all_results:
        st.warning("No company data available.")
        return

    st.markdown(
        '<div class="section-title">COMPANY ANALYSIS</div>',
        unsafe_allow_html=True,
    )

    company_names = [
        result["company"]
        for result in all_results
    ]

    selected_company = st.selectbox(
        "Select Company",
        company_names,
    )

    result = next(
        item for item in all_results
        if item["company"] == selected_company
    )

    score = get_esg_score(result)
    status = get_status(result)

    col1, col2, col3 = st.columns(3)

    with col1:
        st.markdown(
            f"""
            <div class="metric-card">
                <div class="metric-label">ESG SCORE</div>
                <div class="metric-value">{score:.2f}</div>
                <div class="metric-description">
                    Overall sustainability score
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with col2:
        st.markdown(
            f"""
            <div class="metric-card">
                <div class="metric-label">SUSTAINABILITY STATUS</div>
                <div class="metric-value" style="font-size:23px;">
                    {status}
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with col3:
        st.markdown(
            f"""
            <div class="metric-card">
                <div class="metric-label">COMPANY</div>
                <div class="metric-value" style="font-size:23px;">
                    {selected_company}
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    # KPI results

    st.markdown(
        '<div class="section-title">KPI RESULTS</div>',
        unsafe_allow_html=True,
    )

    kpis = result.get("kpis", {})

    if kpis:

        for category, values in kpis.items():

            st.subheader(category)

            if isinstance(values, dict):

                rows = [
                    {
                        "KPI": key,
                        "Value": value,
                    }
                    for key, value in values.items()
                ]

                st.dataframe(
                    pd.DataFrame(rows),
                    use_container_width=True,
                    hide_index=True,
                )

            else:
                st.write(values)

    else:
        st.info("No KPI data available.")

    # Basic analysis

    st.markdown(
        '<div class="section-title">ANALYSIS</div>',
        unsafe_allow_html=True,
    )

    analysis = result.get("analysis", {})

    if analysis:
        analysis_rows = []
        insights = []

        for key, value in analysis.items():
            label = key.replace("_", " ").title()

            if isinstance(value, list) and "insight" in key.lower():
                insights.extend(value)
            elif isinstance(value, dict):
                for detail_key, detail_value in value.items():
                    detail_label = detail_key.replace("_", " ").title()
                    analysis_rows.append({
                        "Detail": f"{label}  -  {detail_label}",
                        "Value": detail_value,
                    })
            elif isinstance(value, list):
                analysis_rows.append({
                    "Detail": label,
                    "Value": ", ".join(str(item) for item in value),
                })
            else:
                analysis_rows.append({
                    "Detail": label,
                    "Value": value,
                })

        if insights:
            st.markdown("**Key insights**")
            for insight in insights:
                st.markdown(f"- {insight}")

        if analysis_rows:
            st.dataframe(
                pd.DataFrame(analysis_rows),
                use_container_width=True,
                hide_index=True,
            )
    else:
        st.info("No analysis data available.")

    # Advanced analysis

    st.markdown(
        '<div class="section-title">ADVANCED ANALYSIS</div>',
        unsafe_allow_html=True,
    )

    advanced = result.get("advanced_analysis", {})

    if advanced:
        advanced_rows = [
            {
                "Metric": key.replace("_", " ").title(),
                "Value": value,
            }
            for key, value in advanced.items()
        ]
        st.dataframe(
            pd.DataFrame(advanced_rows),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("No advanced analysis available.")


# ============================================================
# ESG COMPARISON
# ============================================================

def show_comparison(all_results):

    show_header()

    if not all_results:
        st.warning("No company data available.")
        return

    st.markdown(
        '<div class="section-title">ESG COMPANY COMPARISON</div>',
        unsafe_allow_html=True,
    )

    data = pd.DataFrame([
        {
            "Company": result["company"],
            "ESG Score": get_esg_score(result),
            "Status": get_status(result),
        }
        for result in all_results
    ])

    fig = px.bar(
        data,
        x="Company",
        y="ESG Score",
        color="Status",
        text="ESG Score",
        title="ESG Score by Company",
    )

    fig.update_traces(
        texttemplate="<b>%{text:.2f}</b>",
        textposition="outside",
        textfont=dict(size=14, color="#0b1f33"),
    )

    fig.update_layout(
        height=max(500, 65 * len(data) + 130),
        autosize=True,
        paper_bgcolor="white",
        plot_bgcolor="white",
        font=dict(color="#0b1f33", size=14, family="Arial, sans-serif"),
        title=dict(
            text="<b>ESG Score by Company</b>",
            font=dict(size=20, color="#0b1f33", family="Arial, sans-serif"),
            x=0.01,
            xanchor="left",
        ),
        legend=dict(
            font=dict(size=13, color="#0b1f33", family="Arial, sans-serif"),
            title=dict(text="Status", font=dict(size=13, color="#0b1f33")),
        ),
        margin=dict(l=55, r=85, t=65, b=90),
        xaxis=dict(title=dict(text="Company", font=dict(size=15, color="#0b1f33")), tickfont=dict(size=13, color="#0b1f33"), automargin=True, tickangle=-25),
        yaxis=dict(title=dict(text="ESG Score", font=dict(size=15, color="#0b1f33")), tickfont=dict(size=13, color="#0b1f33"), automargin=True),
    )

    st.plotly_chart(
        fig,
        use_container_width=True,
        config={"responsive": True},
    )

    st.dataframe(
        data,
        use_container_width=True,
        hide_index=True,
    )


# ============================================================
# KPI ANALYTICS
# ============================================================

def _kpi_card(label, value, unit=""):
    display = f"{value:,.1f}" if isinstance(value, (int, float)) and not float(value).is_integer() else f"{value:,.0f}" if isinstance(value, (int, float)) else str(value)
    st.markdown(
        f'<div class="metric-card kpi-summary-card"><div class="metric-label">{label}</div>'
        f'<div class="metric-value" style="font-size:24px;">{display}</div>'
        f'<div class="metric-description">{unit}</div></div>',
        unsafe_allow_html=True,
    )


def _kpi_chart(frame, y, title, y_label, kind="line", color=None):
    if frame is None or frame.empty or not y:
        st.info(f"No source data available for {title.lower()}.")
        return
    y = [column for column in y if column in frame.columns]
    if not y:
        st.info(f"No source data available for {title.lower()}.")
        return
    chart = frame.copy()
    if "Reporting_Period" not in chart.columns and "Supplier_ID" not in chart.columns:
        chart.insert(0, "Reporting_Period", range(1, len(chart) + 1))
    x = "Reporting_Period" if "Reporting_Period" in chart.columns else "Supplier_ID"
    label = "Reporting period" if x == "Reporting_Period" else "Supplier ID"
    series_labels = {column: column.replace("_", " ") for column in y}
    chart_labels = {x: label, **{column: y_label for column in y}}
    plot = px.bar(chart, x=x, y=y, barmode="group", title=title, labels=chart_labels, color_discrete_sequence=color) if kind == "bar" else px.line(chart, x=x, y=y, markers=True, title=title, labels=chart_labels, color_discrete_sequence=color)
    for trace in plot.data:
        if trace.name in series_labels:
            trace.name = series_labels[trace.name]
    plot.update_layout(
        height=360,
        legend_title_text="Measure", hovermode="x unified",
    )
    plot.update_traces(hovertemplate="%{x}<br>%{fullData.name}: %{y:,.2f}<extra></extra>")
    style_kpi_figure(plot)
    st.plotly_chart(plot, use_container_width=True, config={"responsive": True})


def _percentage_indicator(label, value):
    percent = max(0.0, min(100.0, float(value or 0)))
    col1, col2 = st.columns([1, 4])
    col1.metric(label, f"{value:.1f}%")
    col2.progress(percent / 100.0, text=f"{label}: {percent:.1f}%")


def show_kpi_analytics(all_results):
    show_header()
    if not all_results:
        st.warning("No company data available.")
        return

    st.markdown('<div class="section-title kpi-analytics-page-title">KPI ANALYTICS</div>', unsafe_allow_html=True)
    names = [result["company"] for result in all_results]
    selected_name = st.selectbox("Company", names, key="kpi_company_filter") if len(names) > 1 else names[0]
    result = next(item for item in all_results if item["company"] == selected_name)
    kpis = result.get("kpis", {})
    charts = result.get("kpi_chart_data", {})
    if not kpis:
        st.info("No KPI data available.")
        return

    card_specs = [
        ("Energy", "Total_Energy_kWh", "kWh"),
        ("Water", "Water_Used_m3", "m?"),
        ("Waste", "Total_Waste_Tonnes", "tonnes"),
        ("HR", "Employee_Count", "employees"),
        ("Safety", "Accident_Count", "recordable incidents"),
        ("Supplier", "Supplier_Count", "suppliers"),
        ("Procurement", "Sustainable_Material_Quantity", "sustainable material ? tonnes"),
    ]
    st.markdown("#### ESG KPI Summary")
    card_columns = st.columns(4)
    for index, (category, key, unit) in enumerate(card_specs):
        with card_columns[index % len(card_columns)]:
            _kpi_card(category, kpis.get(category, {}).get(key, 0), unit)

    st.markdown('<div class="kpi-category-heading">Category Analytics</div>', unsafe_allow_html=True)
    tabs = st.tabs(["Energy", "Water", "Waste", "HR", "Safety", "Supplier", "Procurement"], key="kpi_analytics_category_tabs")
    with tabs[0]:
        values = kpis.get("Energy", {})
        _percentage_indicator("Renewable energy", values.get("Renewable_Energy_%", 0))
        cols = st.columns(2)
        for col, label, key in zip(cols, ("Electricity consumed", "Solar generation"), ("Electricity_kWh", "Solar_kWh")):
            col.metric(label, f"{values.get(key, 0):,.0f} kWh")
        a, b = st.columns(2)
        with a:
            _kpi_chart(charts.get("Energy"), ["Electricity_kWh", "Solar_kWh", "Renewable_Energy_kWh"], "Monthly electricity and renewable energy", "Energy (kWh)", "line")
        with b:
            _kpi_chart(charts.get("Energy"), ["Carbon_Emissions_tCO2e"], "Monthly carbon emissions", "Emissions (tCO?e)", "bar")
    with tabs[1]:
        values = kpis.get("Water", {})
        _percentage_indicator("Water recycling", values.get("Water_Recycling_%", 0))
        _kpi_chart(charts.get("Water"), ["Water_Consumed_m3", "Water_Used_m3", "Recycled_Water_m3"], "Monthly water use and recycling", "Water (m?)", "bar")
    with tabs[2]:
        values = kpis.get("Waste", {})
        _percentage_indicator("Waste recycling", values.get("Waste_Recycling_%", 0))
        _kpi_chart(charts.get("Waste"), ["Total_Waste_Tonnes", "Recycled_Waste_Tonnes", "Hazardous_Waste_Tonnes"], "Monthly waste generated and treated", "Waste (tonnes)", "bar")
    with tabs[3]:
        hr_data = charts.get("HR")
        a, b = st.columns(2)
        with a:
            if hr_data is not None and not hr_data.empty and "Department" in hr_data.columns:
                training = hr_data.groupby("Department", as_index=False)["Training_Hours"].sum() if "Training_Hours" in hr_data.columns else pd.DataFrame()
                if not training.empty:
                    fig = px.bar(training, x="Department", y="Training_Hours", title="Training hours by department", labels={"Training_Hours": "Training hours"}, color="Department", hover_data={"Training_Hours": ":,.1f"})
                    fig.update_layout(height=360, showlegend=False)
                    style_kpi_figure(fig)
                    st.plotly_chart(fig, use_container_width=True, config={"responsive": True})
                else:
                    st.info("Training hours are not available.")
            else:
                _kpi_chart(hr_data, ["Training_Hours"], "Training hours", "Hours", "bar")
        with b:
            people = charts.get("HR_Employees_By_Department")
            if people is not None and not people.empty:
                fig = px.bar(people, x="Department", y="Employees", title="Employees by department", labels={"Employees": "Employees"}, color="Department", hover_data={"Employees": True})
                fig.update_layout(height=360, showlegend=False)
                style_kpi_figure(fig)
                st.plotly_chart(fig, use_container_width=True, config={"responsive": True})
            else:
                st.info("Department headcount data is not available.")
        st.caption(f"Total training: {kpis.get('HR', {}).get('Total_Training_Hours', 0):,.1f} hours ? Average: {kpis.get('HR', {}).get('Average_Training_Hours', 0):,.1f} hours per record")
    with tabs[4]:
        safety = charts.get("Safety")
        a, b = st.columns(2)
        with a:
            _kpi_chart(safety, ["Recordable_Incidents"], "Recordable incidents by month", "Incidents", "bar")
        with b:
            _kpi_chart(safety, ["Lost_Days", "Affected_Employees"], "Workplace impact by month", "Count / days", "line")
    with tabs[5]:
        values = kpis.get("Supplier", {})
        _percentage_indicator("ISO 14001 certified suppliers", values.get("ISO14001_Certified_%", 0))
        supplier_data = charts.get("Supplier")
        _kpi_chart(supplier_data, ["ESG_Score"], "Supplier ESG scores", "ESG score (0?100)", "bar")
        st.caption(f"Average supplier ESG score: {values.get('Average_ESG_Score', 0):,.1f} / 100 ? Suppliers assessed: {values.get('Supplier_Count', 0):,}")
    with tabs[6]:
        values = kpis.get("Procurement", {})
        st.metric("Sustainable material purchased", f"{values.get('Sustainable_Material_Quantity', 0):,.1f} tonnes")
        _percentage_indicator("Average sustainable material share", values.get("Average_Sustainable_Material_%", 0))
        _kpi_chart(charts.get("Procurement"), ["Sustainable_Material_Quantity"], "Sustainable material quantity by month", "Quantity (tonnes)", "bar")
        _kpi_chart(charts.get("Procurement"), ["Sustainable_Material_Percent", "Sustainable_Material_%"], "Sustainable material share by month", "Share (%)", "line")
        st.caption(f"Total procurement spend: {values.get('Purchase_Amount', 0):,.0f} INR")


# ============================================================
# DATA QUALITY
# ============================================================

@st.cache_data(show_spinner=False, ttl=300, max_entries=64)
def _analyze_quality_workbook(company_name, file_path, modified_ns, file_size):
    """Cache read-only quality analysis until the authorized workbook changes."""
    sheet_rows = []
    detail_frames = {}
    for sheet_name, frame in _load_company_workbook_cached(
        file_path, modified_ns, file_size
    ).items():
        report = data_quality.analyze_frame(frame)
        sheet_rows.append({"Dataset": company_name, "Sheet": sheet_name, **report["metrics"]})
        if not report["flagged_preview"].empty:
            flagged = report["flagged_preview"].copy()
            flagged.insert(0, "Source row", [int(i) + 2 for i in flagged.index])
            detail_frames[f"{company_name} {sheet_name} flagged"] = flagged
        if not report["duplicate_preview"].empty:
            duplicates = report["duplicate_preview"].copy()
            duplicates.insert(0, "Source row", [int(i) + 2 for i in duplicates.index])
            detail_frames[f"{company_name} {sheet_name} duplicates"] = duplicates
    return sheet_rows, detail_frames


def _quality_company_heading_html(company_name):
    """Render one consistently styled, safely escaped company heading."""
    return (
        '<div style="color:#0b1f33;font-size:1.5rem;font-weight:900;line-height:1.3;'
        'border-bottom:2px solid #16856f;padding:0.25rem 0 0.65rem;margin:0.2rem 0 1rem;'
        f'overflow-wrap:anywhere">{html.escape(str(company_name))}</div>'
    )


def _quality_metric_card_html(label, value, *, score=False):
    """Build a high-contrast metric card without relying on Streamlit internals."""
    safe_label = html.escape(str(label))
    safe_value = html.escape(str(value))
    if score:
        card_style = (
            "background:#e7f2ef;border:2px solid #16856f;min-height:8rem;"
            "padding:1.2rem 1.4rem;margin:0.35rem 0 1rem;"
        )
        label_style = "color:#123c35;font-size:1.05rem;font-weight:800;line-height:1.4;"
        value_style = "color:#0b1f33;font-size:2.35rem;font-weight:900;line-height:1.2;margin-top:0.45rem;"
    else:
        card_style = (
            "background:#ffffff;border:1px solid #9bb9af;min-height:6.5rem;"
            "padding:1rem 1.1rem;margin:0.35rem 0 0.65rem;"
        )
        label_style = "color:#243f52;font-size:0.95rem;font-weight:800;line-height:1.4;"
        value_style = "color:#0b1f33;font-size:1.65rem;font-weight:900;line-height:1.25;margin-top:0.45rem;"
    return (
        f'<div role="group" aria-label="{safe_label}" '
        f'style="{card_style}border-radius:12px;box-shadow:0 3px 12px rgba(11,31,51,0.08);">'
        f'<div style="{label_style}">{safe_label}</div>'
        f'<div style="{value_style}">{safe_value}</div></div>'
    )


def show_data_quality(validation_data):

    show_header()

    st.markdown(
        '<div class="section-title">DATA QUALITY MONITORING</div>',
        unsafe_allow_html=True,
    )

    user = st.session_state.user
    sheet_rows = []
    detail_frames = {}
    if user["role"] == "normal_user":
        companies = database.list_companies(user)
        if companies:
            company = companies[0]
            departments = database.list_departments(user, company["id"])
            department = departments[0] if departments else None
            if department:
                rows = database.list_legacy_rows(user, company["id"], department["id"], limit=100000)
                if rows:
                    frame = pd.DataFrame([json.loads(row["payload_json"]) for row in rows])
                    report = data_quality.analyze_frame(frame, source_row_offset=1)
                    sheet_rows.append({"Dataset": company["name"], "Sheet": department["name"], **report["metrics"]})
                    detail_frames[f"{department['name']} flagged rows"] = report["flagged_preview"]
                    detail_frames[f"{department['name']} duplicate preview"] = report["duplicate_preview"]
    else:
        companies = database.list_companies(user)
        dataset_paths = load_company_config(tuple(company["name"] for company in companies))
        for company_name, relative_path in dataset_paths.items():
            file_path = CONFIG_DIR.parent / relative_path
            if not file_path.is_file():
                continue
            try:
                file_stat = file_path.stat()
                rows, previews = _analyze_quality_workbook(
                    company_name, str(file_path), file_stat.st_mtime_ns, file_stat.st_size
                )
                sheet_rows.extend(rows)
                detail_frames.update(previews)
            except Exception as exc:
                st.warning(f"Could not analyze {company_name}: {exc}")

    history = database.list_cleaning_history(user, limit=500)
    if sheet_rows:
        st.caption(
            "Cleaned Records counts active-workbook rows requiring safe whitespace normalization. "
            "Confirmed removals, changed rows, and skipped review rows are listed separately in Cleaning History."
        )
        for dataset in sorted({row["Dataset"] for row in sheet_rows}):
            with st.container(border=True):
                st.markdown(
                    _quality_company_heading_html(dataset),
                    unsafe_allow_html=True,
                )
                subset = [row for row in sheet_rows if row["Dataset"] == dataset]
                reports = {row["Sheet"]: {"metrics": {key: row.get(key, 0) for key in (
                    "total_records", "valid_records", "duplicate_records", "exact_duplicate_records",
                    "business_key_duplicate_records", "empty_rows", "missing_values", "invalid_records",
                    "cleaned_records", "flagged_records", "missing_by_column", "quality_score")}}
                    for row in subset}
                metrics = data_quality.aggregate_reports(reports)

                # Give the score its own prominent card; the remaining metrics
                # flow into fewer columns on narrow screens through Streamlit's
                # responsive column layout.
                st.markdown(
                    _quality_metric_card_html(
                        "Data Quality Score", f"{metrics.get('quality_score', 0):.2f}%", score=True
                    ),
                    unsafe_allow_html=True,
                )
                metric_keys = [
                    ("total_records", "Total Records"), ("valid_records", "Valid Records"),
                    ("duplicate_records", "Duplicate Records"), ("empty_rows", "Completely Empty Rows"),
                    ("missing_values", "Missing Values"), ("invalid_records", "Invalid Records"),
                    ("cleaned_records", "Cleaned Records"),
                ]
                for offset in range(0, len(metric_keys), 3):
                    columns = st.columns(3)
                    for column, (key, label) in zip(columns, metric_keys[offset:offset + 3]):
                        column.markdown(
                            _quality_metric_card_html(label, metrics.get(key, 0)),
                            unsafe_allow_html=True,
                        )
        st.dataframe(pd.DataFrame(sheet_rows).drop(columns=["missing_by_column"], errors="ignore"), use_container_width=True, hide_index=True)
    else:
        st.info("No authorized ESG dataset rows are available for quality analysis yet.")

    if validation_data:
        for company_name, validation_results in validation_data.items():
            if not isinstance(validation_results, dict):
                continue
            issues = [(sheet, entry) for sheet, entries in validation_results.items()
                      for entry in (entries if isinstance(entries, list) else [entries]) if entry]
            if issues:
                with st.expander(f"Validation findings — {company_name}"):
                    for sheet, issue in issues:
                        st.warning(f"{sheet}: {issue}")

    history_view = [{key: row.get(key) for key in (
        "created_at", "company_name", "department_name", "actor_name", "operation",
        "changed_records", "removed_records", "skipped_records", "flagged_records")} for row in history]
    if history_view:
        st.subheader("Cleaning History")
        st.dataframe(pd.DataFrame(history_view), use_container_width=True, hide_index=True)

    company_label = ", ".join(sorted({row["Dataset"] for row in sheet_rows})) or "Assigned department"
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    safe_name = re.sub(r"[^A-Za-z0-9_-]+", "_", company_label).strip("_") or "esg"
    left, right = st.columns(2)
    left.download_button(
        "Download Excel Data Quality Report",
        lambda: data_quality.excel_report_bytes(
            company_label, user.get("name", "User"), sheet_rows, history_view, detail_frames, generated_at
        ),
        f"{safe_name}_data_quality.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    right.download_button(
        "Download PDF Data Quality Report",
        lambda: data_quality.pdf_report_bytes(
            company_label, user.get("name", "User"), sheet_rows, history_view, generated_at
        ),
        f"{safe_name}_data_quality.pdf",
        "application/pdf",
    )


# ============================================================
# REPORTS
# ============================================================

# DATASET MANAGEMENT
# ============================================================

def show_dataset_management():
    """Manage company registry and ESG workbooks with recoverable backups."""
    st.markdown('<div class="section-title">Dataset Management</div>', unsafe_allow_html=True)
    st.info("Add, view, rename, update, or remove company registrations. Before replacing or removing a workbook, the app creates a backup.")

    project_root = CONFIG_DIR.parent
    config_path = CONFIG_DIR / "companies.json"
    companies = load_company_config()

    def save_registry(updated_companies):
        """Atomically save the registry after backing up its previous version."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        if config_path.exists():
            backup_path = config_path.with_name(f"companies.backup.{timestamp}.json")
            shutil.copy2(config_path, backup_path)
        temp_config = config_path.with_name(f"companies.{timestamp}.json.tmp")
        temp_config.write_text(
            json.dumps({"companies": updated_companies}, indent=4, ensure_ascii=False),
            encoding="utf-8",
        )
        temp_config.replace(config_path)
        database.sync_company_registry(updated_companies, st.session_state.user["id"])
        load_esg_data.clear()

    def workbook_is_valid(file_bytes):
        from io import BytesIO
        from src.data_loader import EXPECTED_SHEETS
        workbook = pd.ExcelFile(BytesIO(file_bytes))
        missing = [sheet for sheet in EXPECTED_SHEETS if sheet not in workbook.sheet_names]
        return missing

    def safe_company_stem(name):
        return re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_-") or "company"

    # VIEW
    st.subheader("Registered companies")
    registry_rows = []
    for name, relative_path in companies.items():
        file_path = project_root / relative_path
        registry_rows.append({
            "Company": name,
            "Dataset": relative_path,
            "Status": "Available" if file_path.is_file() else "File missing",
        })
    if registry_rows:
        st.dataframe(pd.DataFrame(registry_rows), use_container_width=True, hide_index=True)
    else:
        st.caption("No companies are registered yet.")

    add_tab, edit_tab, update_tab, delete_tab = st.tabs([
        "➕ Add Company", "✏️ Edit Company Name", "🔄 Update Dataset", "🗑️ Delete Company"
    ])

    # ADD
    with add_tab:
        st.subheader("Add a new company")
        new_name = st.text_input("Company name", placeholder="e.g. Example Industries", key="crud_add_name")
        new_file = st.file_uploader("Upload ESG Excel workbook", type=["xlsx"], key="crud_add_file")
        if new_file is not None:
            file_bytes = new_file.getvalue()
            try:
                missing = workbook_is_valid(file_bytes)
                if missing:
                    st.error("Missing required sheets: " + ", ".join(missing))
                else:
                    st.success("Workbook structure is valid.")
                    from io import BytesIO
                    from src.data_loader import EXPECTED_SHEETS
                    st.caption(f"Preview: {EXPECTED_SHEETS[0]}")
                    st.dataframe(pd.read_excel(BytesIO(file_bytes), sheet_name=EXPECTED_SHEETS[0]).head(5), use_container_width=True)
                    if st.button("Add company", type="primary", key="crud_add_button"):
                        cleaned_name = new_name.strip()
                        if not cleaned_name:
                            st.error("Please enter a company name.")
                        elif any(existing.casefold() == cleaned_name.casefold() for existing in companies):
                            st.error("A company with this name is already registered.")
                        else:
                            raw_dir = project_root / "data" / "raw"
                            raw_dir.mkdir(parents=True, exist_ok=True)
                            destination = raw_dir / f"{safe_company_stem(cleaned_name)}.xlsx"
                            if destination.exists():
                                destination = raw_dir / f"{safe_company_stem(cleaned_name)}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
                            destination.write_bytes(file_bytes)
                            updated = dict(companies)
                            updated[cleaned_name] = destination.relative_to(project_root).as_posix()
                            save_registry(updated)
                            st.success(f"{cleaned_name} added successfully.")
                            st.rerun()
            except Exception as error:
                st.error(f"Could not read this workbook: {error}")

    # EDIT NAME
    with edit_tab:
        st.subheader("Edit company details")
        if companies:
            selected_edit = st.selectbox("Select company", list(companies), key="crud_edit_select")
            revised_name = st.text_input("New company name", value=selected_edit, key="crud_edit_name")
            if st.button("Save company name", type="primary", key="crud_edit_button"):
                cleaned = revised_name.strip()
                if not cleaned:
                    st.error("Company name cannot be empty.")
                elif cleaned != selected_edit and any(x.casefold() == cleaned.casefold() for x in companies):
                    st.error("Another company already uses this name.")
                else:
                    updated = dict(companies)
                    # Keep the original dataset path unchanged; only rename the registry key.
                    rebuilt = {}
                    for name, path in updated.items():
                        rebuilt[cleaned if name == selected_edit else name] = path
                    save_registry(rebuilt)
                    st.success("Company name updated. Dataset file was not changed.")
                    st.rerun()
        else:
            st.caption("There are no companies to edit.")

    # UPDATE DATASET
    with update_tab:
        st.subheader("Replace a company's ESG workbook")
        st.warning("The current workbook will first be copied to data/backups/dataset_versions. The backup is retained.")
        if companies:
            selected_update = st.selectbox("Select company to update", list(companies), key="crud_update_select")
            update_file = st.file_uploader("Upload replacement ESG workbook", type=["xlsx"], key="crud_update_file")
            if update_file is not None:
                update_bytes = update_file.getvalue()
                try:
                    missing = workbook_is_valid(update_bytes)
                    if missing:
                        st.error("Missing required sheets: " + ", ".join(missing))
                    else:
                        st.success("Replacement workbook structure is valid.")
                        if st.button("Back up and update dataset", type="primary", key="crud_update_button"):
                            current_path = project_root / companies[selected_update]
                            if not current_path.is_file():
                                st.error("Current dataset file is missing. Use Add Company or restore the file first.")
                            else:
                                backup_dir = project_root / "data" / "backups" / "dataset_versions"
                                backup_dir.mkdir(parents=True, exist_ok=True)
                                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                                backup_path = backup_dir / f"{current_path.stem}_{timestamp}{current_path.suffix}"
                                shutil.copy2(current_path, backup_path)
                                temp_path = current_path.with_name(f"{current_path.stem}.uploading{current_path.suffix}")
                                temp_path.write_bytes(update_bytes)
                                temp_path.replace(current_path)
                                load_esg_data.clear()
                                st.success(f"Dataset updated. Previous version backed up as {backup_path.relative_to(project_root).as_posix()}.")
                                st.rerun()
                except Exception as error:
                    st.error(f"Could not validate this workbook: {error}")
        else:
            st.caption("There are no companies to update.")

    # DELETE / ARCHIVE
    with delete_tab:
        st.subheader("Remove a company from the registry")
        st.warning("To protect your data, deleting a company does not permanently erase its workbook. It moves the workbook into data/backups/removed_datasets.")
        if companies:
            selected_delete = st.selectbox("Select company to remove", list(companies), key="crud_delete_select")
            confirm_delete = st.checkbox(f"I confirm removing {selected_delete} from the company registry.", key="crud_delete_confirm")
            if st.button("Remove company", type="primary", disabled=not confirm_delete, key="crud_delete_button"):
                updated = dict(companies)
                relative_path = updated.pop(selected_delete)
                dataset_path = project_root / relative_path
                if dataset_path.is_file():
                    archive_dir = project_root / "data" / "backups" / "removed_datasets"
                    archive_dir.mkdir(parents=True, exist_ok=True)
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                    archived_path = archive_dir / f"{dataset_path.stem}_{timestamp}{dataset_path.suffix}"
                    shutil.move(str(dataset_path), str(archived_path))
                save_registry(updated)
                st.success(f"{selected_delete} removed from the registry. Its dataset is archived, not permanently deleted.")
                st.rerun()
        else:
            st.caption("There are no companies to remove.")

    st.caption("All registry changes create a timestamped companies.json backup. Dataset replacements and removals also preserve previous workbook files under data/backups/.")


def show_manual_esg_data():
    user = st.session_state.user
    st.markdown('<div class="section-title">Manual ESG Data Entry</div>', unsafe_allow_html=True)
    companies = database.list_companies()
    allowed = set(database.accessible_company_ids(user))
    companies = [c for c in companies if c["id"] in allowed]
    records = database.list_esg_records(user)
    if user["role"] == "viewer":
        st.caption("Viewer access is read-only.")
    if not companies:
        st.info("No company access is assigned to this account.")
    elif user["role"] != "viewer":
        target = st.selectbox("Company", companies, format_func=lambda x: x["name"], key="manual_company")
        existing = [r for r in records if r["company_id"] == target["id"]]
        edit = st.selectbox("Edit an existing year (optional)", [None] + existing, format_func=lambda x: "Create new record" if x is None else f"{x['financial_year']} • {x['company_name']}", key="manual_edit")
        with st.form("manual_esg_form"):
            year = st.text_input("Financial Year", value=edit["financial_year"] if edit else "FY2025")
            a,b,c = st.columns(3)
            energy=a.number_input("Energy Consumption (kWh)", min_value=0.0, value=float(edit["energy_kwh"]) if edit else 0.0)
            water=b.number_input("Water Consumption (m³)", min_value=0.0, value=float(edit["water_m3"]) if edit else 0.0)
            carbon=c.number_input("Carbon Emissions (tCO₂e)", min_value=0.0, value=float(edit["carbon_tco2e"]) if edit else 0.0)
            a,b,c=st.columns(3)
            waste=a.number_input("Waste Generation (tonnes)", min_value=0.0, value=float(edit["waste_tonnes"]) if edit else 0.0)
            renewable=b.number_input("Renewable Energy (%)", min_value=0.0, max_value=100.0, value=float(edit["renewable_pct"]) if edit else 0.0)
            social=c.number_input("Social Performance (0–100)", min_value=0.0, max_value=100.0, value=float(edit["social_score"]) if edit else 0.0)
            employee=st.text_area("Employee Information", value=edit["employee_info"] if edit else "")
            governance=st.text_area("Governance Information", value=edit["governance_info"] if edit else "")
            save=st.form_submit_button("Update record" if edit else "Save ESG record", type="primary")
        if save:
            try:
                if not year.strip(): raise ValueError("Financial Year is required.")
                database.save_esg_record(user,target["id"],year.strip(),energy,water,carbon,waste,renewable,social,employee,governance,edit["id"] if edit else None)
                st.success("ESG record saved."); st.rerun()
            except Exception as error: st.error(str(error))
    if records:
        st.subheader("Permitted company records")
        st.dataframe(pd.DataFrame(records), use_container_width=True, hide_index=True)
        from io import BytesIO
        output=BytesIO()
        with pd.ExcelWriter(output,engine="openpyxl") as writer: pd.DataFrame(records).to_excel(writer,index=False,sheet_name="ESG Records")
        st.download_button("Download Excel report",output.getvalue(),"esg_records.xlsx","application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def show_company_management():
    user=st.session_state.user
    st.markdown('<div class="section-title">Company Management</div>',unsafe_allow_html=True)
    rows=database.list_companies(active_only=False)
    st.dataframe(pd.DataFrame(rows),use_container_width=True,hide_index=True)
    with st.form("company_add"):
        name=st.text_input("Company name"); path=st.text_input("Dataset path (optional)")
        if st.form_submit_button("Add company"):
            try: database.save_company(name,path or None,user_id=user["id"]); st.success("Company added."); st.rerun()
            except Exception as e: st.error(str(e))
    active=[c for c in rows if c["active"]]
    if active:
        selected=st.selectbox("Edit or archive",active,format_func=lambda x:x["name"])
        revised=st.text_input("Company name",value=selected["name"],key="company_revised")
        if st.button("Save company changes"):
            try: database.save_company(revised,selected["dataset_path"],selected["id"],user["id"]); st.success("Updated."); st.rerun()
            except Exception as e: st.error(str(e))
        if st.button("Archive company",type="secondary"):
            database.archive_company(selected["id"],user["id"]); st.success("Company archived; records are retained."); st.rerun()


def show_user_management():
    actor=st.session_state.user
    st.markdown('<div class="section-title">User Management</div>',unsafe_allow_html=True)
    users=database.list_users()
    st.dataframe(pd.DataFrame(users),use_container_width=True,hide_index=True)
    pending=[u for u in users if u["status"]=="pending"]
    if pending:
        u=st.selectbox("Pending registration",pending,format_func=lambda x:f"{x['name']} • {x['email']}")
        role=st.selectbox("Assign role",["normal_user","viewer"])
        company_list=database.list_companies(); company=st.selectbox("Assign company",company_list,format_func=lambda x:x["name"])
        edit=st.checkbox("Allow this user to edit ESG data")
        c1,c2=st.columns(2)
        if c1.button("Approve"):
            database.approve_user(u["id"],True,role,actor["id"])
            if company: database.set_permission(u["id"],company["id"],edit,actor["id"])
            st.success("User approved with the selected access."); st.rerun()
        if c2.button("Reject"):
            database.approve_user(u["id"],False,role,actor["id"]); st.rerun()
    st.subheader("Company permissions")
    active_users=[u for u in users if u["role"]!="super_admin" and u["status"]=="approved"]
    companies=database.list_companies()
    if active_users:
        role_user=st.selectbox("Update user role",active_users,format_func=lambda x:x["email"],key="role_user")
        new_role=st.selectbox("Role",["normal_user","viewer"],index=0 if role_user["role"]=="normal_user" else 1,key="new_role")
        if st.button("Update role"):
            database.update_user_role(role_user["id"],new_role,actor["id"]); st.success("Role updated."); st.rerun()
    if active_users and companies:
        u=st.selectbox("User",active_users,format_func=lambda x:x["email"],key="perm_user")
        co=st.selectbox("Company",companies,format_func=lambda x:x["name"],key="perm_company")
        can_edit=st.checkbox("Can edit ESG records",key="perm_edit")
        if st.button("Save permission"):
            database.set_permission(u["id"],co["id"],can_edit,actor["id"]); st.success("Permission saved.")
        if st.button("Revoke company access"):
            database.remove_permission(u["id"],co["id"],actor["id"]); st.success("Company access revoked."); st.rerun()
    st.subheader("Audit log")
    st.dataframe(pd.DataFrame(database.audit_logs()),use_container_width=True,hide_index=True)


def show_database_management():
    st.markdown('<div class="section-title">Database Management</div>',unsafe_allow_html=True)
    st.write(f"SQLite database: `{database.DB_PATH}`")
    st.write(f"Companies: {len(database.list_companies(active_only=False))} • Users: {len(database.list_users())}")
    if st.button("Create database backup"):
        st.success(f"Backup created: {database.backup_database()}")
    st.subheader("Safe database restore")
    st.warning("A database restore replaces current database state with the selected verified backup. A fresh backup of the current database is created first. Type RESTORE DATABASE to continue.")
    restore_candidates = database.discover_database_backups()
    if restore_candidates:
        restore_source = st.selectbox(
            "Verified backup candidate",
            restore_candidates,
            format_func=lambda item: f"{item.parent.name}/{item.name} ({item.stat().st_size:,} bytes)",
            key="database_restore_candidate",
        )
        with st.form("database_restore_form"):
            restore_confirmation = st.text_input("Type RESTORE DATABASE to confirm")
            restore_submitted = st.form_submit_button("Restore selected database backup", type="primary")
        if restore_submitted:
            try:
                result = database.restore_database(user, restore_source, restore_confirmation)
                st.session_state["database_restore_notice"] = (
                    f"Database restored from {Path(result['restored_from']).name}. Current database backup saved at "
                    f"{Path(result['pre_restore_backup']).name}. Sign in again."
                )
                st.session_state.authenticated = False
                st.session_state.pop("user", None)
                st.session_state.pop("main_nav", None)
                st.rerun()
            except Exception as exc:
                st.error(f"Restore was not performed: {exc}")
    else:
        st.info("No SQLite backups are available in the active or legacy backup folders.")
    st.subheader("Legacy migration status")
    status=database.migration_status()
    if status: st.dataframe(pd.DataFrame(status),use_container_width=True,hide_index=True)
    else: st.info("Legacy migration has not been run. Use `python -m src.migrate_legacy` after backing up the source data.")


# Tenant-safe workspace pages. These definitions intentionally replace the older
# global company/user management renderers while keeping their underlying analytics.
def show_company_workspace():
    user = st.session_state.user
    company = database.company_profile(user)
    company_id = company["id"]
    st.markdown('<div class="section-title">Company Workspace</div>', unsafe_allow_html=True)
    profile_tab, departments_tab, people_tab, audit_tab = st.tabs(["Company Profile", "Departments", "Employees", "Audit History"])
    with profile_tab:
        with st.form("manager_company_profile"):
            name = st.text_input("Company name", value=company["name"])
            legal_name = st.text_input("Legal company name", value=company.get("legal_name", ""))
            registration_number = st.text_input("Registration number / CIN", value=company.get("registration_number", ""))
            col1, col2 = st.columns(2)
            industry = col1.text_input("Industry / business sector", value=company.get("industry", ""))
            company_type = col2.text_input("Company type", value=company.get("company_type", ""))
            size_options = ["Small", "Medium", "Large"]
            stored_size = company.get("company_size", "")
            company_size = st.selectbox("Company size", size_options, index=size_options.index(stored_size) if stored_size in size_options else 0)
            col1, col2 = st.columns(2)
            country = col1.text_input("Country", value=company.get("country", ""))
            state = col2.text_input("State / region", value=company.get("state", ""))
            col1, col2 = st.columns(2)
            city = col1.text_input("City", value=company.get("city", ""))
            postal_code = col2.text_input("PIN / ZIP code", value=company.get("postal_code", ""))
            registered_address = st.text_area("Complete registered address", value=company.get("registered_address", ""))
            col1, col2 = st.columns(2)
            official_email = col1.text_input("Official company email", value=company.get("official_email", ""))
            official_phone = col2.text_input("Official phone number", value=company.get("official_phone", ""))
            website = st.text_input("Website", value=company.get("website", ""))
            reporting_year = st.text_input("Financial reporting year", value=company.get("financial_reporting_year", ""))
            if company.get("logo_blob"):
                st.image(company["logo_blob"], caption="Current company logo", width=120)
            logo_upload = st.file_uploader("Replace company logo (optional)", type=["png", "jpg", "jpeg", "webp"], key="manager_company_logo")
            st.markdown("#### Company Manager")
            col1, col2 = st.columns(2)
            manager_name = col1.text_input("Manager name", value=company.get("manager_name", ""))
            manager_designation = col2.text_input("Designation", value=company.get("manager_designation", ""))
            st.text_input("Manager login email", value=company.get("manager_email", ""), disabled=True)
            manager_mobile = st.text_input("Manager mobile number", value=company.get("manager_mobile_number", ""))
            manager_username = st.text_input("Manager username", value=company.get("manager_username", ""))
            if st.form_submit_button("Save company profile"):
                try:
                    profile_updates = {
                        "name": name, "legal_name": legal_name, "registration_number": registration_number,
                        "industry": industry, "company_type": company_type, "company_size": company_size,
                        "country": country, "state": state, "city": city, "registered_address": registered_address,
                        "postal_code": postal_code, "official_email": official_email, "official_phone": official_phone,
                        "website": website, "financial_reporting_year": reporting_year,
                        "manager_name": manager_name, "designation": manager_designation,
                        "mobile_number": manager_mobile, "username": manager_username,
                    }
                    if logo_upload is not None:
                        from PIL import Image
                        from io import BytesIO
                        logo_data = logo_upload.getvalue()
                        if len(logo_data) > 2 * 1024 * 1024:
                            raise ValueError("Company logo must be no larger than 2 MB.")
                        with Image.open(BytesIO(logo_data)) as image:
                            image.verify()
                        with Image.open(BytesIO(logo_data)) as image:
                            if image.format not in ("PNG", "JPEG", "WEBP"):
                                raise ValueError("Upload a valid PNG, JPEG, or WebP logo.")
                            if image.width > 6000 or image.height > 6000 or image.width * image.height > 12_000_000:
                                raise ValueError("Logo dimensions are too large. Use an image under 12 megapixels.")
                            safe_image = image.convert("RGBA" if image.mode in ("RGBA", "LA") else "RGB")
                            safe_buffer = BytesIO()
                            safe_image.save(safe_buffer, format="PNG", optimize=True)
                            profile_updates["logo_blob"] = safe_buffer.getvalue()
                    database.update_company_registration_profile(user, company_id, profile_updates)
                    st.success("Company profile updated.")
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))
        st.caption("Company Manager access is limited to this company.")
    with departments_tab:
        depts = database.list_departments(user, company_id, active_only=False)
        st.dataframe(pd.DataFrame(depts), use_container_width=True, hide_index=True)
        with st.form("manager_add_department"):
            department_name = st.text_input("New department")
            if st.form_submit_button("Add department"):
                try:
                    database.create_department(user, company_id, department_name)
                    st.success("Department created.")
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))
        active_depts = [d for d in depts if d["active"] and d["name"].casefold() != "general"]
        if active_depts:
            dept = st.selectbox("Archive department", active_depts, format_func=lambda d: d["name"], key="archive_department_select")
            if st.button("Archive department"):
                try:
                    database.archive_department(user, company_id, dept["id"])
                    st.success("Department archived.")
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))
    with people_tab:
        users = database.list_users(user)
        pending = [u for u in users if u["role"] == "normal_user" and u["status"] == "pending"]
        if pending:
            employee = st.selectbox("Employee access request", pending, format_func=lambda u: f"{u['name']} • {u['email']}", key="manager_pending_employee")
            active_depts = [d for d in database.list_departments(user, company_id) if d["active"]]
            requested = next((i for i,d in enumerate(active_depts) if d["id"] == employee["department_id"]), 0)
            department = st.selectbox("Assign department", active_depts, index=requested, format_func=lambda d: d["name"], key="manager_approve_department")
            can_edit = st.checkbox("Allow ESG data edits and uploads", key="manager_approve_edit")
            approve_col, reject_col = st.columns(2)
            if approve_col.button("Approve employee", type="primary"):
                database.approve_user(user, employee["id"], True, department["id"], can_edit)
                st.success("Employee approved with department-scoped access.")
                st.rerun()
            if reject_col.button("Reject request"):
                database.approve_user(user, employee["id"], False)
                st.rerun()
        else:
            st.info("No pending employee requests.")
        employees = [u for u in users if u["role"] == "normal_user" and u["status"] == "approved"]
        if employees:
            st.subheader("Approved employees")
            st.dataframe(pd.DataFrame(employees), use_container_width=True, hide_index=True)
            employee = st.selectbox("Update employee access", employees, format_func=lambda u: f"{u['name']} • {u['email']}", key="manager_edit_employee")
            active_depts = [d for d in database.list_departments(user, company_id) if d["active"]]
            index = next((i for i,d in enumerate(active_depts) if d["id"] == employee["department_id"]), 0)
            department = st.selectbox("Department", active_depts, index=index, format_func=lambda d: d["name"], key="manager_edit_department")
            edit_ok = st.checkbox("Can edit and upload ESG data", value=bool(employee["can_edit_esg"]), key="manager_edit_permission")
            if st.button("Save employee access"):
                database.assign_user_department(user, employee["id"], department["id"], edit_ok)
                st.success("Employee access updated.")
                st.rerun()
            reset_target=st.selectbox("Issue a password reset token",employees,format_func=lambda u:f"{u['name']} • {u['email']}",key="manager_reset_employee")
            if st.button("Create one-time reset token",key="manager_issue_reset"):
                try:
                    issued=database.issue_password_reset(user,reset_target["id"])
                    st.warning("Share this token with the employee through a trusted private channel. It expires in 30 minutes.")
                    st.code(issued["token"])
                except Exception as exc: st.error(str(exc))
    with audit_tab:
        logs = database.audit_logs(user)
        st.dataframe(pd.DataFrame(logs), use_container_width=True, hide_index=True)


def _persist_immutable_artifact(relative_path, content):
    """Write a versioned file once; never replace a stored source or clean copy."""
    return persist_immutable_artifact(CONFIG_DIR.parent, relative_path, content)


def _preview_cleaning_report(report, key):
    metrics = report["metrics"]
    cols = st.columns(4)
    for col, (label, value) in zip(cols, (
        ("Total Records", metrics["total_records"]),
        ("Duplicate Candidates", metrics["duplicate_records"]),
        ("Removable Duplicates", metrics["removable_duplicate_records"]),
        ("Completely Empty Rows", metrics["empty_rows"]),
    )):
        col.metric(label, value)
    st.metric("Quality Score", f"{metrics['quality_score']:.2f}%")
    cols = st.columns(4)
    for col, (label, value) in zip(cols, (
        ("Valid Records", metrics["valid_records"]), ("Missing Values", metrics["missing_values"]),
        ("Invalid Records", metrics["invalid_records"]), ("Whitespace Corrections", metrics["cleaned_records"]),
    )):
        col.metric(label, value)
    if report["column_issues"]:
        st.dataframe(pd.DataFrame(report["column_issues"]), use_container_width=True, hide_index=True)
    if report["metrics"]["missing_by_column"]:
        st.write("Missing cells by field", report["metrics"]["missing_by_column"])
    incomplete = metrics.get("incomplete_duplicate_records", 0)
    if incomplete:
        st.info(
            f"{incomplete} duplicate candidate row(s) are incomplete and will be retained for review. "
            "Only complete duplicate rows are eligible for automatic removal."
        )
    for label, frame in (("Duplicate candidates", report["duplicate_preview"]),
                         ("Completely empty rows", report["empty_preview"]),
                         ("Flagged records retained for review", report["flagged_preview"])):
        with st.expander(f"{label} ({len(frame)} previewed)"):
            if frame.empty:
                st.caption("None found.")
            else:
                preview = frame.copy()
                preview.insert(0, "Source row", [int(i) + 2 for i in preview.index])
                st.dataframe(preview, use_container_width=True, hide_index=True)


def _workbook_bytes(frames):
    return validated_workbook_bytes(frames)


def show_dataset_management():
    user = st.session_state.user
    company = database.company_profile(user)
    company_id = company["id"]
    st.markdown('<div class="section-title">Company Dataset Management</div>', unsafe_allow_html=True)
    st.info("Every uploaded workbook is quality-checked. Original Excel files and prior versions remain unchanged; cleaning creates a new active copy.")
    st.write("Current workbook:", company.get("dataset_path") or "No workbook uploaded")
    uploaded = st.file_uploader("Upload or update this company's ESG workbook", type=["xlsx"], key="manager_company_workbook")
    if uploaded is not None:
        from io import BytesIO
        from src.data_loader import EXPECTED_SHEETS
        try:
            raw = uploaded.getvalue()
            workbook = pd.ExcelFile(BytesIO(raw))
            missing = [name for name in EXPECTED_SHEETS if name not in workbook.sheet_names]
            if missing:
                st.error("Workbook is missing required sheets: " + ", ".join(missing))
            else:
                frames = {name: pd.read_excel(BytesIO(raw), sheet_name=name) for name in EXPECTED_SHEETS}
                st.subheader("Automatic quality check")
                initial_reports = {name: data_quality.analyze_frame(frame) for name, frame in frames.items()}
                st.dataframe(pd.DataFrame([{"Sheet": name, **report["metrics"]} for name, report in initial_reports.items()]).drop(columns=["missing_by_column"], errors="ignore"), use_container_width=True, hide_index=True)
                selected_sheet = st.selectbox("Sheet to review", list(frames), key="workbook_quality_sheet")
                business_keys = st.multiselect(
                    "Optional exact business key columns (all selected fields must match)",
                    list(frames[selected_sheet].columns), key=f"workbook_business_keys_{selected_sheet}",
                )
                report = data_quality.analyze_frame(frames[selected_sheet], business_keys=business_keys)
                st.subheader("Cleaning Preview by Sheet")
                for sheet_name in frames:
                    sheet_report = report if sheet_name == selected_sheet else initial_reports[sheet_name]
                    sheet_metrics = sheet_report["metrics"]
                    affected = any(sheet_metrics.get(key, 0) for key in (
                        "duplicate_records", "empty_rows", "missing_values", "invalid_records",
                        "cleaned_records", "flagged_records",
                    ))
                    with st.expander(
                        f"{sheet_name} — {int(sheet_metrics['total_records'])} records"
                        + (" · review/cleaning items found" if affected else " · no issues found"),
                        expanded=(sheet_name == selected_sheet),
                    ):
                        _preview_cleaning_report(sheet_report, sheet_name)
                remove_empty = st.checkbox("Remove completely empty rows", key="workbook_remove_empty")
                remove_exact = st.checkbox("Remove exact duplicate rows, keeping the first copy", key="workbook_remove_exact")
                remove_keys = st.checkbox("Remove duplicate records matching every selected business key", disabled=not business_keys, key="workbook_remove_keys")
                destructive = remove_empty or remove_exact or (remove_keys and bool(business_keys))
                st.caption("Removal options take effect only after the confirmation below. Incomplete or invalid records stay in the workbook for review.")
                confirm = st.checkbox("I reviewed the duplicate/empty-row previews and confirm these removals", key="workbook_clean_confirm") if destructive else True
                if st.button("Clean preview and store new workbook version", type="primary", key="manager_clean_workbook"):
                    if destructive and not confirm:
                        st.error("Confirm the preview before removing any rows.")
                    else:
                        try:
                            original_hash = hashlib.sha256(raw).hexdigest()
                            base = Path("data") / "company_datasets" / str(company_id)
                            original_rel, cleaned_rel = versioned_cleaning_paths(
                                base, original_hash, uploaded.name, ".xlsx",
                            )
                            # A complete, integrity-checked DB backup is required before cleaning.
                            verified_backup = database.create_verified_cleaning_backup(user, company_id)
                            cleaned_frames = {}
                            details_by_sheet = {}
                            for sheet_name, frame in frames.items():
                                use_keys = business_keys if sheet_name == selected_sheet else []
                                cleaned, details, _ = data_quality.apply_cleaning(
                                    frame, remove_empty=remove_empty, remove_exact_duplicates=remove_exact,
                                    business_keys=use_keys,
                                    remove_business_key_duplicates=bool(remove_keys and sheet_name == selected_sheet),
                                    confirmed=confirm,
                                )
                                cleaned_frames[sheet_name] = cleaned
                                details_by_sheet[sheet_name] = details
                            cleaned_bytes = _workbook_bytes(cleaned_frames)
                            _persist_immutable_artifact(original_rel, raw)
                            _persist_immutable_artifact(cleaned_rel, cleaned_bytes)
                            metrics = list(details_by_sheet.values())
                            report_json = {
                                "company": company["name"], "user": user.get("name"),
                                "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                "business_key_sheet": selected_sheet, "business_key_columns": business_keys,
                                "options": {"remove_empty": remove_empty, "remove_exact_duplicates": remove_exact, "remove_business_key_duplicates": bool(remove_keys)},
                                "sheets": details_by_sheet,
                            }
                            metadata = {
                                "source_name": uploaded.name, "source_hash": original_hash,
                                "operation": " + ".join(name for name, selected in (
                                    ("safe whitespace normalization", True), ("remove empty rows", remove_empty),
                                    ("remove exact duplicates", remove_exact), ("remove business-key duplicates", bool(remove_keys))) if selected),
                                "backup_id": verified_backup["id"], "backup_path": verified_backup["path"], "original_path": original_rel.as_posix(),
                                "cleaned_path": cleaned_rel.as_posix(), "changed_records": sum(int(row["changed_records"]) for row in metrics),
                                "removed_records": sum(int(row["removed_records"]) for row in metrics),
                                "skipped_records": sum(int(row["skipped_records"]) for row in metrics),
                                "flagged_records": sum(int(row["flagged_records"]) for row in metrics),
                                "report": report_json,
                            }
                            database.commit_company_dataset_cleaning(user, company_id, cleaned_rel.as_posix(), metadata)
                            load_esg_data.clear()
                            st.success(
                                "Quality-checked workbook stored as the new active version. "
                                f"Normalized {metadata['changed_records']} row(s), removed "
                                f"{metadata['removed_records']} confirmed row(s), retained "
                                f"{metadata['skipped_records']} incomplete duplicate candidate(s) for review, "
                                f"and flagged {metadata['flagged_records']} row(s). The original file is preserved."
                            )
                            st.rerun()
                        except Exception as exc:
                            st.error(f"Workbook cleaning was not committed: {exc}")
        except Exception as exc:
            st.error(f"Could not read this workbook: {exc}")

    history = [row for row in database.list_cleaning_history(user, limit=200) if row["department_id"] is None and row["original_path"]]
    if history:
        st.subheader("Cleaning History")
        st.caption("Each entry records the active workbook version, source version, operator, timestamp, and cleaning actions. Restoring an original changes only the active pointer.")
        history_view = [{
            "Created At": row["created_at"],
            "User": row.get("actor_name", ""),
            "Source Version": Path(row["original_path"]).name,
            "Output Version": Path(row["cleaned_path"]).name,
            "Actions": row["operation"],
            "Changed": row["changed_records"],
            "Removed": row["removed_records"],
            "Skipped for Review": row["skipped_records"],
            "Flagged": row["flagged_records"],
        } for row in history]
        st.dataframe(pd.DataFrame(history_view), use_container_width=True, hide_index=True)
        with st.expander("Restore a preserved original workbook"):
            selected = st.selectbox("Cleaning run", history, format_func=lambda row: f"{row['created_at']} — {row['source_name']}", key="restore_original_workbook_run")
            st.caption("Restore switches the active workbook to the preserved original upload. It does not delete the cleaned copy or historical data.")
            if st.button("Restore original workbook", key="restore_original_workbook"):
                try:
                    database.restore_company_dataset(user, company_id, selected["id"], "RESTORE ORIGINAL")
                    load_esg_data.clear()
                    st.success("The active workbook now points to the preserved original file.")
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))


def show_manual_esg_data():
    user = st.session_state.user
    role = user["role"]
    st.markdown('<div class="section-title">Manual ESG Data</div>', unsafe_allow_html=True)
    if role == "company_manager":
        company = database.company_profile(user)
        companies = [company]
    elif role == "normal_user":
        companies = database.list_companies(user)
    else:
        companies = database.list_companies(user)
    records = database.list_esg_records(user)
    if role in ("company_manager", "normal_user"):
        company = companies[0] if role == "normal_user" else st.selectbox("Company", companies, format_func=lambda c: c["name"])
        company_id = company["id"]
        departments = database.list_departments(user, company_id)
        if role == "normal_user":
            department = departments[0] if departments else None
            can_edit = bool(user.get("can_edit_esg"))
        else:
            department = st.selectbox("Department", departments, format_func=lambda d: d["name"])
            can_edit = True
        company_records = [r for r in records if r["company_id"] == company_id and (department is None or r["department_id"] == department["id"])]
        if role == "normal_user":
            st.caption(f"Company: {company['name']} • Assigned department: {department['name'] if department else 'not assigned'}")
        if can_edit and department:
            existing = st.selectbox("Update an existing year (optional)", [None] + company_records,
                format_func=lambda r: "Create a new record" if r is None else f"{r['financial_year']} • {r['department_name']}", key="tenant_esg_edit")
            with st.form("tenant_esg_entry"):
                year = st.text_input("Financial Year", value=existing["financial_year"] if existing else "FY2025")
                a,b,c = st.columns(3)
                energy=a.number_input("Energy Consumption (kWh)", min_value=0.0, value=float(existing["energy_kwh"]) if existing else 0.0)
                water=b.number_input("Water Consumption (m³)", min_value=0.0, value=float(existing["water_m3"]) if existing else 0.0)
                carbon=c.number_input("Carbon Emissions (tCO₂e)", min_value=0.0, value=float(existing["carbon_tco2e"]) if existing else 0.0)
                a,b,c=st.columns(3)
                waste=a.number_input("Waste Generation (tonnes)", min_value=0.0, value=float(existing["waste_tonnes"]) if existing else 0.0)
                renewable=b.number_input("Renewable Energy (%)", min_value=0.0, max_value=100.0, value=float(existing["renewable_pct"]) if existing else 0.0)
                social=c.number_input("Social Performance (0–100)", min_value=0.0, max_value=100.0, value=float(existing["social_score"]) if existing else 0.0)
                employee=st.text_area("Employee Information", value=existing["employee_info"] if existing else "")
                governance=st.text_area("Governance Information", value=existing["governance_info"] if existing else "")
                save=st.form_submit_button("Update ESG record" if existing else "Save ESG record", type="primary")
            if save:
                try:
                    record_id=database.save_esg_record(user,company_id,department["id"],year,energy,water,carbon,waste,renewable,social,employee,governance,existing["id"] if existing else None)
                    st.success("Saved ESG record " + str(record_id))
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))
            st.subheader("Department data upload")
            st.caption("Upload one CSV or one-sheet Excel file for the selected department. Rows are stored with this company and department ID.")
            file = st.file_uploader("Department CSV or Excel", type=["csv", "xlsx"], key="department_upload")
            if file is not None:
                try:
                    raw = file.getvalue()
                    from io import BytesIO
                    if file.name.lower().endswith(".csv"):
                        frame = pd.read_csv(BytesIO(raw))
                    else:
                        workbook = pd.ExcelFile(BytesIO(raw))
                        if len(workbook.sheet_names) != 1:
                            raise ValueError("Upload a workbook containing exactly one department sheet.")
                        frame = pd.read_excel(BytesIO(raw), sheet_name=workbook.sheet_names[0])
                    st.subheader("Automatic quality check")
                    initial_report = data_quality.analyze_frame(frame)
                    _preview_cleaning_report(initial_report, department["name"])
                    business_keys = st.multiselect(
                        "Optional exact business key columns (all selected fields must match)",
                        list(frame.columns), key=f"department_business_keys_{company_id}_{department['id']}",
                    )
                    report = data_quality.analyze_frame(frame, business_keys=business_keys)
                    if business_keys:
                        st.write("Duplicate preview using selected business keys")
                        if report["duplicate_preview"].empty:
                            st.caption("No duplicate candidates found for those exact key values.")
                        else:
                            st.dataframe(report["duplicate_preview"], use_container_width=True, hide_index=True)
                    remove_empty = st.checkbox("Remove completely empty rows", key=f"department_remove_empty_{company_id}_{department['id']}")
                    remove_exact = st.checkbox("Remove exact duplicate rows, keeping the first copy", key=f"department_remove_exact_{company_id}_{department['id']}")
                    remove_keys = st.checkbox("Remove duplicate records matching every selected business key", disabled=not business_keys, key=f"department_remove_keys_{company_id}_{department['id']}")
                    destructive = remove_empty or remove_exact or (remove_keys and bool(business_keys))
                    confirm = st.checkbox("I reviewed the preview and confirm these row removals", key=f"department_clean_confirm_{company_id}_{department['id']}") if destructive else True
                    if st.button("Import department rows", type="primary", key="department_upload_submit"):
                        if destructive and not confirm:
                            st.error("Confirm the preview before removing any rows.")
                        else:
                            try:
                                digest = hashlib.sha256(raw).hexdigest()
                                cleaned_frame, details, _ = data_quality.apply_cleaning(
                                    frame, remove_empty=remove_empty, remove_exact_duplicates=remove_exact,
                                    business_keys=business_keys,
                                    remove_business_key_duplicates=bool(remove_keys and business_keys),
                                    confirmed=confirm,
                                    source_row_offset=2,
                                )
                                verified_backup = database.create_verified_cleaning_backup(user, company_id, department["id"])
                                base = Path("data") / "uploads" / str(company_id) / str(department["id"])
                                if file.name.lower().endswith(".csv"):
                                    cleaned_bytes = cleaned_frame.to_csv(index=False).encode("utf-8-sig")
                                    cleaned_ext = ".csv"
                                else:
                                    clean_output = BytesIO()
                                    with pd.ExcelWriter(clean_output, engine="openpyxl") as writer:
                                        cleaned_frame.to_excel(writer, index=False, sheet_name=department["name"][:31])
                                    cleaned_bytes = clean_output.getvalue()
                                    cleaned_ext = ".xlsx"
                                original_rel, cleaned_rel = versioned_cleaning_paths(
                                    base, digest, file.name, cleaned_ext,
                                )
                                _persist_immutable_artifact(original_rel, raw)
                                _persist_immutable_artifact(cleaned_rel, cleaned_bytes)
                                cleaned_digest = hashlib.sha256(cleaned_bytes).hexdigest()
                                report_json = {
                                    "company": company["name"], "department": department["name"],
                                    "user": user.get("name"),
                                    "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                    "business_key_columns": business_keys,
                                    "options": {"remove_empty": remove_empty, "remove_exact_duplicates": remove_exact,
                                                "remove_business_key_duplicates": bool(remove_keys)},
                                    **details,
                                }
                                metadata = {
                                    "source_name": file.name, "source_hash": digest,
                                    "operation": " + ".join(name for name, selected in (
                                        ("safe whitespace normalization", True), ("remove empty rows", remove_empty),
                                        ("remove exact duplicates", remove_exact), ("remove business-key duplicates", bool(remove_keys))) if selected),
                                    "backup_id": verified_backup["id"], "backup_path": verified_backup["path"], "original_path": original_rel.as_posix(),
                                    "cleaned_path": cleaned_rel.as_posix(), "changed_records": details["changed_records"],
                                    "removed_records": details["removed_records"], "skipped_records": details["skipped_records"],
                                    "flagged_records": details["flagged_records"], "report": report_json,
                                }
                                imported = database.import_department_rows(
                                    user, department["id"], cleaned_rel.name, cleaned_digest,
                                    cleaned_frame.to_dict(orient="records"), cleaning_metadata=metadata,
                                )
                                st.success(f"Imported {imported} new department rows. Changed {details['changed_records']}, removed {details['removed_records']} confirmed row(s), skipped {details['skipped_records']}, and flagged {details['flagged_records']} for review. Original file preserved.")
                                st.rerun()
                            except Exception as exc:
                                st.error(f"Upload cleaning/import failed; existing records were not removed: {exc}")
                except Exception as exc:
                    st.error(f"Upload could not be imported: {exc}")
    else:
        if role == "viewer":
            st.caption("Viewer mode is read-only and includes all active companies.")
            companies = database.list_companies(user)
            selected = st.selectbox("Company filter", [None] + companies, format_func=lambda c: "All companies" if c is None else c["name"])
            records = database.list_esg_records(user, selected["id"] if selected else None)
        else:
            st.info("Platform Super Admins manage platform security and do not access tenant ESG records.")
            return
    if records:
        st.subheader("Authorized ESG records")
        frame=pd.DataFrame(records)
        st.dataframe(frame,use_container_width=True,hide_index=True)
        from io import BytesIO
        output=BytesIO()
        with pd.ExcelWriter(output,engine="openpyxl") as writer: frame.to_excel(writer,index=False,sheet_name="ESG Records")
        st.download_button("Download Excel",output.getvalue(),"authorized_esg_records.xlsx","application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def show_department_page():
    user=st.session_state.user
    st.markdown('<div class="section-title">My Department</div>',unsafe_allow_html=True)
    company=database.list_companies(user)[0]
    department=database.list_departments(user,company["id"])[0]
    st.write(f"**{company['name']}** · **{department['name']}**")
    rows=database.list_legacy_rows(user,company["id"],department["id"])
    if rows:
        raw=pd.DataFrame([json.loads(row["payload_json"]) for row in rows])
        st.metric("Available department rows",len(rows))
        st.dataframe(raw,use_container_width=True,hide_index=True)
        from src.report_generator import dataframe_csv_bytes
        st.download_button("Download department CSV",dataframe_csv_bytes(raw),"department_data.csv","text/csv")
    else:
        st.info("No migrated or uploaded rows are available for this department.")
    show_manual_esg_data()


def show_user_management():
    # Company Managers manage only their company's employee requests and assignments.
    show_company_workspace()


def show_company_management():
    show_company_workspace()


def show_platform_administration():
    user=st.session_state.user
    st.markdown('<div class="section-title">Platform Administration</div>',unsafe_allow_html=True)
    users=database.platform_users(user)
    pending=[u for u in users if u["role"]=="viewer" and u["status"]=="pending"]
    st.subheader("Viewer approvals")
    if pending:
        viewer=st.selectbox("Pending auditor or government account",pending,format_func=lambda u:f"{u['name']} • {u['email']}")
        c1,c2=st.columns(2)
        if c1.button("Approve read-only Viewer"):
            database.approve_user(user,viewer["id"],True); st.success("Viewer approved for read-only access across active companies."); st.rerun()
        if c2.button("Reject Viewer"):
            database.approve_user(user,viewer["id"],False); st.rerun()
    else:
        st.info("No Viewer requests are pending.")
    st.subheader("Legacy companies without a Company Manager")
    companies=database.platform_company_metadata(user)
    users=database.platform_users(user)
    unassigned=[c for c in companies if c["active"] and not c["manager_user_id"]]
    if unassigned:
        company=st.selectbox("Unmanaged company",unassigned,format_func=lambda c:c["name"])
        candidates=[u for u in users if u["status"] in ("approved","pending") and u["role"] in ("normal_user","company_manager") and u["company_id"] in (None,company["id"])]
        if candidates:
            manager=st.selectbox("Assign an account as Company Manager",candidates,format_func=lambda u:f"{u['name']} • {u['email']} • {u['status']}")
            if st.button("Assign Company Manager"):
                try:
                    database.assign_company_manager(user,company["id"],manager["id"])
                    st.success("Company Manager assigned and account approved.")
                    st.rerun()
                except Exception as exc: st.error(str(exc))
        else:
            st.info("Have the proposed manager submit a Normal User request for this company first, then approve them here for legacy-company management.")
    st.subheader("Tenant registry metadata")
    st.dataframe(pd.DataFrame(companies),use_container_width=True,hide_index=True)
    reset_users=[u for u in users if u["status"]=="approved" and u["role"]!="platform_super_admin"]
    if reset_users:
        st.subheader("Account recovery")
        target=st.selectbox("Issue reset token",reset_users,format_func=lambda u:f"{u['name']} • {u['email']}",key="platform_reset_user")
        if st.button("Create one-time reset token",key="platform_issue_reset"):
            try:
                issued=database.issue_password_reset(user,target["id"])
                st.warning("Share this token privately. It expires in 30 minutes.")
                st.code(issued["token"])
            except Exception as exc: st.error(str(exc))


def show_database_management():
    user=st.session_state.user
    st.markdown('<div class="section-title">Platform Database Management</div>',unsafe_allow_html=True)
    if user["role"]!="platform_super_admin":
        st.error("Platform administration access is required.")
        return
    with database.connect() as conn:
        stats={table:conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in ("companies","departments","users","esg_records","legacy_dataset_rows","audit_logs")}
    st.write(f"SQLite: `{database.DB_PATH}`")
    st.dataframe(pd.DataFrame([stats]),use_container_width=True,hide_index=True)
    if st.button("Create database backup"):
        st.success(f"Backup created: {database.backup_database()}")
    st.subheader("Safe database restore")
    st.warning("A restore replaces the active database with a verified backup. A fresh backup of the current database is created first. Type RESTORE DATABASE to continue.")
    restore_candidates=database.discover_database_backups()
    if restore_candidates:
        restore_source=st.selectbox(
            "Verified backup candidate",restore_candidates,
            format_func=lambda item:f"{item.parent.name}/{item.name} ({item.stat().st_size:,} bytes)",
            key="database_restore_candidate",
        )
        with st.form("database_restore_form"):
            restore_confirmation=st.text_input("Type RESTORE DATABASE to confirm")
            restore_submitted=st.form_submit_button("Restore selected database backup",type="primary")
        if restore_submitted:
            try:
                result=database.restore_database(user,restore_source,restore_confirmation)
                st.session_state["database_restore_notice"]=(
                    f"Database restored from {Path(result['restored_from']).name}. Current database backup saved at "
                    f"{Path(result['pre_restore_backup']).name}. Sign in again."
                )
                st.session_state.authenticated=False
                st.session_state.pop("user",None)
                st.session_state.pop("main_nav",None)
                st.rerun()
            except Exception as exc:
                st.error(f"Restore was not performed: {exc}")
    else:
        st.info("No SQLite backups are available in the active or legacy backup folders.")
    st.subheader("Migration history")
    history=database.migration_status(user)
    st.dataframe(pd.DataFrame(history),use_container_width=True,hide_index=True) if history else st.info("No migration history recorded.")
    st.subheader("Platform audit history")
    st.dataframe(pd.DataFrame(database.audit_logs(user)),use_container_width=True,hide_index=True)


def show_reports(all_results):
    show_header()
    st.markdown('<div class="section-title">ESG REPORTS</div>', unsafe_allow_html=True)
    user = st.session_state.user
    visible_companies = database.list_companies(user)
    reporting_periods = {company["name"]: company.get("financial_reporting_year", "") for company in visible_companies}

    for result in all_results or []:
        company = result["company"]
        report = make_json_serializable(create_company_report(
            company, result.get("kpis", {}), result.get("analysis", {}),
            advanced_analysis=result.get("advanced_analysis", {}),
            reporting_period=reporting_periods.get(company) or None,
            validation=result.get("validation", {}),
        ))
        report["generated_at_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        chart_data = result.get("kpi_chart_data", {})
        safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", company.lower()).strip("_-") or "company"
        with st.expander(f"\U0001f4c4 {company} ESG Report"):
            st.json(report)
            report_json = json.dumps(report, indent=2, ensure_ascii=False, default=str, allow_nan=False).encode("utf-8")
            st.download_button("Download JSON", report_json, f"{safe}_esg_report.json", "application/json", key=f"tenant_json_{safe}")
            excel = company_report_excel_bytes(report, chart_data)
            st.download_button("Download Excel", excel, f"{safe}_esg_report.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key=f"tenant_xlsx_{safe}")
            st.download_button(
                "Download PDF",
                lambda report=report, chart_data=chart_data: company_report_pdf_bytes(report, chart_data),
                f"{safe}_esg_report.pdf",
                "application/pdf",
                key=f"tenant_pdf_{safe}",
            )

    records = database.list_esg_records(user)
    st.subheader("Manual and department ESG records")
    if records:
        frame = pd.DataFrame(records)
        st.dataframe(frame, use_container_width=True, hide_index=True)
        excel = manual_records_excel_bytes(records)
        st.download_button("Download ESG records Excel", excel, "esg_records.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        pdf = manual_records_pdf_bytes(records)
        st.download_button("Download ESG PDF", pdf, "esg_records.pdf", "application/pdf")
    else:
        st.info("There are no manual ESG records available for your account yet.")

    # The database API enforces company and department scope for this role.
    # Keep the existing company selector for multi-company viewers.
    if len(visible_companies) > 1:
        report_company = st.selectbox("Company data search", visible_companies, format_func=lambda company: company["name"], key="report_company_search")
        report_company_id = report_company["id"]
    else:
        report_company_id = visible_companies[0]["id"] if visible_companies else None
    legacy = database.list_legacy_rows(user, company_id=report_company_id, limit=100000) if report_company_id else []
    if legacy:
        sheets = sorted({row["sheet_name"] for row in legacy})
        selected_sheet = st.selectbox("Department data sheet", ["All sheets"] + sheets, key="report_sheet_search")
        if selected_sheet != "All sheets":
            legacy = [row for row in legacy if row["sheet_name"] == selected_sheet]
        raw_frame = pd.DataFrame([{**json.loads(row["payload_json"]), "Company": row["company_name"], "Department": row["department_name"], "Source sheet": row["sheet_name"]} for row in legacy])
        st.subheader("Authorized migrated and uploaded data")
        from src.report_generator import dataframe_csv_bytes
        st.download_button("Download source data CSV", dataframe_csv_bytes(raw_frame), "authorized_source_data.csv", "text/csv")
    if not all_results and not records and not legacy:
        st.info("No reports are available for your account yet.")


# ============================================================
# MAIN APPLICATION
# ============================================================

def show_mfa_enrollment(user):
    import qrcode
    from io import BytesIO

    st.title("Secure your administrator account")
    st.info("Authenticator MFA is required for Company Managers and Platform Super Admins. Protected pages stay locked until enrollment is complete.")
    try:
        uri = database.mfa_enrollment_uri(user["id"], user["mfa_enrollment_token"])
    except Exception:
        st.error("MFA setup is unavailable. Contact the platform operator to configure the encryption key, then sign in again.")
        st.stop()
    image = qrcode.make(uri)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    st.image(buffer.getvalue(), caption="Scan with Google Authenticator or Microsoft Authenticator")
    st.caption("If you cannot scan the QR code, add the account manually in your authenticator app.")
    st.code(uri.split("secret=", 1)[1].split("&", 1)[0])
    with st.form("mfa_enrollment_form"):
        code = st.text_input("Enter the six-digit authenticator code", max_chars=6)
        submitted = st.form_submit_button("Enable MFA")
    if submitted:
        try:
            result = database.complete_mfa_enrollment(user["id"], user["mfa_enrollment_token"], code,
                                                     client_ip=_auth_client_ip())
            st.session_state.user = result["user"]
            st.session_state["mfa_recovery_codes_pending"] = result["recovery_codes"]
            st.rerun()
        except Exception:
            st.error("That code could not be verified. Check the authenticator time and try again.")


def show_mfa_recovery_codes():
    st.title("Save your recovery codes")
    st.warning("Each code works once. Save these codes somewhere private; they will not be shown again.")
    st.code("\n".join(st.session_state.get("mfa_recovery_codes_pending", [])))
    if st.button("I saved my recovery codes", type="primary"):
        st.session_state.pop("mfa_recovery_codes_pending", None)
        st.rerun()


def show_security_settings(user):
    st.title("Account security")
    st.success("Authenticator MFA is enabled for this administrator account.")
    st.markdown("To replace a lost authenticator, enter your current password and verify with an authenticator or unused recovery code. This resets MFA and requires enrollment again at the next sign-in.")
    with st.form("mfa_reset_form"):
        password = st.text_input("Current password", type="password")
        code = st.text_input("Authenticator or unused recovery code")
        submitted = st.form_submit_button("Reset MFA enrollment")
    if submitted:
        try:
            database.reset_mfa(user, password, code, client_ip=_auth_client_ip())
            st.session_state.authenticated = False
            st.session_state.pop("user", None)
            st.session_state.pop("main_nav", None)
            st.success("MFA was reset. Sign in again to enroll a new authenticator.")
            st.rerun()
        except Exception:
            st.error("MFA reset failed. Verify your password and authenticator or recovery code.")

if not st.session_state.authenticated:
    if st.session_state.get("database_restore_notice"):
        st.success(st.session_state.pop("database_restore_notice"))
    if st.session_state.get("session_expired_notice"):
        st.error(st.session_state.pop("session_expired_notice"))
    show_login_page()

else:

    if st.session_state.get("user", {}).get("mfa_enrollment_required"):
        show_mfa_enrollment(st.session_state.user)
        st.stop()

    try:
        user = database.validate_session_user(st.session_state.user)
        st.session_state.user = user
    except (PermissionError, KeyError, TypeError):
        st.session_state.authenticated = False
        st.session_state.pop("user", None)
        st.session_state.pop("main_nav", None)
        st.session_state["session_expired_notice"] = "Your account is no longer active. Please sign in again."
        st.rerun()

    if st.session_state.get("mfa_recovery_codes_pending"):
        show_mfa_recovery_codes()
        st.stop()

    page = show_sidebar()

    try:
        role = user["role"]
        all_results, ranking, validation_data = [], [], {}
        if role == "platform_super_admin":
            if page == "🛡️ Platform Administration":
                show_platform_administration()
            elif page == "🗄️ Database Management":
                show_database_management()
            elif page == "🔐 Security":
                show_security_settings(user)
        elif role == "normal_user":
            if page == "🧑‍🔧 My Department":
                show_department_page()
        elif page == "🔐 Security" and role == "company_manager":
            show_security_settings(user)
        elif page == "🏭 Company Workspace":
            show_company_workspace()
        elif page == "📤 Dataset Management":
            show_dataset_management()
        elif page == "📝 Manual ESG Data":
            show_manual_esg_data()
        elif page == "🏭 Company Management":
            show_company_workspace()
        elif page == "👥 User Management":
            show_company_workspace()
        elif page == "🗄️ Database Management":
            show_database_management()
        else:
            accessible = database.list_companies(user)
            allowed_names = tuple(company["name"] for company in accessible)
            with st.status("Loading ESG analytics...", expanded=False) as load_status:
                source_signature = _company_source_signature(allowed_names)
                all_results, ranking, validation_data = load_esg_data(allowed_names, source_signature)
                load_status.update(label="ESG analytics ready", state="complete", expanded=False)

        if page == "🏠 Dashboard":

            show_dashboard(
                all_results,
                ranking,
            )

        elif page == "🏢 Company Analysis":

            show_company_analysis(
                all_results,
            )

        elif page == "📊 ESG Comparison":

            show_comparison(
                all_results,
            )

        elif page == "📈 KPI Analytics":

            show_kpi_analytics(
                all_results,
            )

        elif page == "⚠️ Data Quality":

            show_data_quality(
                validation_data,
            )

        elif page == "📄 Reports":

            show_reports(
                all_results,
            )

    except Exception as error:

        st.error("Unable to load ESG analytics.")
        st.exception(error)


# ============================================================
# FOOTER
# ============================================================

if st.session_state.authenticated:

    st.markdown(
        """
        <div class="app-footer">
            ESG Analytics Platform •
            Python • Streamlit • ESG Intelligence
        </div>
        """,
        unsafe_allow_html=True,
    )
