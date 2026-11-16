import json
import database

from config import (
    CONFIG_DIR,
    CO2_DATA_FILE,
    ENABLE_EXTERNAL_CO2,
    ENABLE_REPORTS,
    ENABLE_VISUALIZATIONS,
    REPORT_DIR,
    VISUALIZATION_DIR,
)

from src.analysis import analyze_company

from src.advanced_analysis import (
    perform_advanced_analysis,
    rank_companies,
)

from src.authentication import authenticate_user

from src.data_loader import (
    load_company_data,
    load_external_co2_data,
)

from src.kpi_calculator import calculate_kpis
from src.preprocessing import preprocess_company_data
from src.report_generator import save_company_report
from src.validation import validate_company_data
from src.visualization import generate_all_visualizations


# ==========================================
# LOAD COMPANY CONFIGURATION
# ==========================================

def load_company_config(user):
    """Return only companies authorized for this CLI analytics session."""
    if user.get("role") not in ("company_manager", "viewer"):
        raise PermissionError("The CLI analytics pipeline is available only to Company Managers and read-only Viewers.")
    return {row["name"]: row["dataset_path"] for row in database.list_companies(user) if row.get("dataset_path")}


# ==========================================
# PROCESS ONE COMPANY
# ==========================================

def process_company(
    company_name,
    file_path,
    external_co2_data=None,
):
    """Run the ESG processing pipeline for one company."""

    print(f"\nProcessing: {company_name}")

    # --------------------------------------
    # LOAD COMPANY DATA
    # --------------------------------------

    company_data = load_company_data(
        file_path
    )

    # --------------------------------------
    # VALIDATE COMPANY DATA
    # --------------------------------------

    validation_results = validate_company_data(
        company_data
    )

    for sheet_name, issues in validation_results.items():

        if issues:

            print(f"{sheet_name}:")

            for issue in issues:

                print(
                    f"  - {issue}"
                )

    # --------------------------------------
    # PREPROCESS COMPANY DATA
    # --------------------------------------

    processed_data = preprocess_company_data(
        company_data
    )

    # --------------------------------------
    # CALCULATE ESG KPIs
    # --------------------------------------

    kpis = calculate_kpis(
        processed_data
    )

    # --------------------------------------
    # ANALYZE COMPANY
    # --------------------------------------

    analysis_results = analyze_company(
        company_name,
        kpis,
        external_co2_data,
    )

    # --------------------------------------
    # ADVANCED ESG ANALYSIS
    # --------------------------------------

    advanced_analysis = perform_advanced_analysis(
        company_name,
        kpis,
    )

    print(
        f"ESG Score: "
        f"{advanced_analysis['esg_score']}"
    )

    print(
        f"Sustainability Status: "
        f"{advanced_analysis['sustainability_status']}"
    )

    # --------------------------------------
    # SAVE ESG REPORT
    # --------------------------------------

    if ENABLE_REPORTS:

        report_file = save_company_report(
            company_name,
            kpis,
            analysis_results,
            REPORT_DIR,
        )

        print(
            f"Report saved: {report_file}"
        )

    # --------------------------------------
    # COMPANY COMPLETED
    # --------------------------------------

    print(
        f"{company_name} processed successfully."
    )

    # --------------------------------------
    # RETURN RESULTS
    # --------------------------------------

    return {
        "company": company_name,
        "kpis": kpis,
        "analysis": analysis_results,
        "advanced_analysis": advanced_analysis,
    }


# ==========================================
# MAIN PIPELINE
# ==========================================

def main():
    """Run the complete ESG analytics pipeline."""

    # ======================================
    # USER AUTHENTICATION
    # ======================================

    user = authenticate_user()
    if not user:
        return
    if user.get("role") not in ("company_manager", "viewer"):
        print("This role cannot run the multi-company CLI analytics pipeline.")
        return

    # ======================================
    # LOAD EXTERNAL CO2 DATA DYNAMICALLY
    # ======================================

    external_co2_data = None

    if ENABLE_EXTERNAL_CO2:

        external_co2_path = CO2_DATA_FILE

        if external_co2_path.exists():

            external_co2_data = (
                load_external_co2_data(
                    external_co2_path
                )
            )

            print(
                f"\nExternal CO2 dataset loaded: "
                f"{len(external_co2_data)} rows"
            )

        else:

            print(
                f"\nExternal CO2 dataset not found: "
                f"{external_co2_path}"
            )

            print(
                "Continuing without external CO2 data."
            )

    else:

        print(
            "\nExternal CO2 data feature is disabled."
        )

    # ======================================
    # LOAD COMPANIES DYNAMICALLY
    # ======================================

    companies = load_company_config(user)

    all_results = []

    # ======================================
    # PROCESS ALL COMPANIES
    # ======================================

    for company_name, relative_path in companies.items():

        # ----------------------------------
        # CONVERT RELATIVE PATH
        # TO PROJECT PATH
        # ----------------------------------

        file_path = (
            CONFIG_DIR.parent
            / relative_path
        )

        # ----------------------------------
        # CHECK COMPANY FILE EXISTS
        # ----------------------------------

        if not file_path.exists():

            print(
                f"\nSkipping {company_name}: "
                f"file not found -> {file_path}"
            )

            continue

        # ----------------------------------
        # PROCESS COMPANY
        # ----------------------------------

        result = process_company(
            company_name,
            file_path,
            external_co2_data,
        )

        all_results.append(
            result
        )

    # ======================================
    # COMPANY ESG RANKING
    # ======================================

    if all_results:

        company_ranking = rank_companies(
            all_results
        )

        print("\n" + "=" * 50)
        print("          COMPANY ESG RANKING")
        print("=" * 50)

        for company in company_ranking:

            print(
                f"{company['rank']}. "
                f"{company['company']} "
                f"- ESG Score: "
                f"{company['esg_score']}"
            )

    else:

        print(
            "\nNo companies were processed."
        )

    # ======================================
    # GENERATE VISUALIZATIONS
    # ======================================

    if ENABLE_VISUALIZATIONS:

        if all_results:

            generate_all_visualizations(
                all_results,
                VISUALIZATION_DIR,
            )

            print(
                "\nVisualizations generated successfully."
            )

        else:

            print(
                "\nNo company data available "
                "for visualization."
            )

    else:

        print(
            "\nVisualization feature is disabled."
        )

    # ======================================
    # PIPELINE COMPLETED
    # ======================================

    print(
        "\nESG Analytics Pipeline "
        "completed successfully."
    )

    return all_results


# ==========================================
# APPLICATION ENTRY POINT
# ==========================================

if __name__ == "__main__":
    main()
