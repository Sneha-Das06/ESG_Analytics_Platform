# ==========================================
# ESG SCORE CALCULATION
# ==========================================

def calculate_esg_score(kpis):
    """
    Calculate an overall ESG score
    based on Environmental, Social,
    and Governance KPIs.
    """

    scores = []

    # ======================================
    # ENVIRONMENTAL SCORE
    # ======================================

    environmental_scores = []

    # --------------------------------------
    # Renewable Energy
    # --------------------------------------

    energy_data = kpis.get(
        "Energy",
        {}
    )

    renewable_energy = energy_data.get(
        "Renewable_Energy_%",
        0
    )

    if renewable_energy > 0:

        environmental_scores.append(
            min(
                float(renewable_energy),
                100
            )
        )

    # --------------------------------------
    # Water Recycling
    # --------------------------------------

    water_data = kpis.get(
        "Water",
        {}
    )

    water_recycling = water_data.get(
        "Water_Recycling_%",
        0
    )

    if water_recycling > 0:

        environmental_scores.append(
            min(
                float(water_recycling),
                100
            )
        )

    # --------------------------------------
    # Waste Recycling
    # --------------------------------------

    waste_data = kpis.get(
        "Waste",
        {}
    )

    waste_recycling = waste_data.get(
        "Waste_Recycling_%",
        0
    )

    if waste_recycling > 0:

        environmental_scores.append(
            min(
                float(waste_recycling),
                100
            )
        )

    if environmental_scores:

        environmental_score = (
            sum(environmental_scores)
            / len(environmental_scores)
        )

        scores.append(
            environmental_score
        )

    # ======================================
    # SOCIAL SCORE
    # ======================================

    social_scores = []

    # --------------------------------------
    # Employee Training
    # --------------------------------------

    hr_data = kpis.get(
        "HR",
        {}
    )

    training_hours = hr_data.get(
        "Average_Training_Hours",
        0
    )

    if training_hours > 0:

        # 40 hours or more = full score

        training_score = min(
            float(training_hours) / 40 * 100,
            100
        )

        social_scores.append(
            training_score
        )

    # --------------------------------------
    # Workplace Safety
    # --------------------------------------

    safety_data = kpis.get(
        "Safety",
        {}
    )

    accident_count = safety_data.get(
        "Accident_Count",
        0
    )

    employee_count = hr_data.get(
        "Employee_Count",
        0
    )

    if employee_count > 0:

        accident_rate = (
            accident_count
            / employee_count
            * 100
        )

        safety_score = max(
            0,
            100 - accident_rate * 10
        )

        social_scores.append(
            safety_score
        )

    if social_scores:

        social_score = (
            sum(social_scores)
            / len(social_scores)
        )

        scores.append(
            social_score
        )

    # ======================================
    # GOVERNANCE SCORE
    # ======================================

    governance_scores = []

    # --------------------------------------
    # Supplier ESG Score
    # --------------------------------------

    supplier_data = kpis.get(
        "Supplier",
        {}
    )

    supplier_esg_score = supplier_data.get(
        "Average_ESG_Score",
        0
    )

    if supplier_esg_score > 0:

        governance_scores.append(
            min(
                float(supplier_esg_score),
                100
            )
        )

    # --------------------------------------
    # ISO 14001 Certification
    # --------------------------------------

    iso_certified = supplier_data.get(
        "ISO14001_Certified_%",
        0
    )

    if iso_certified > 0:

        governance_scores.append(
            min(
                float(iso_certified),
                100
            )
        )

    # --------------------------------------
    # Sustainable Procurement
    # --------------------------------------

    procurement_data = kpis.get(
        "Procurement",
        {}
    )

    sustainable_material = procurement_data.get(
        "Average_Sustainable_Material_%",
        0
    )

    if sustainable_material > 0:

        governance_scores.append(
            min(
                float(sustainable_material),
                100
            )
        )

    if governance_scores:

        governance_score = (
            sum(governance_scores)
            / len(governance_scores)
        )

        scores.append(
            governance_score
        )

    # ======================================
    # FINAL ESG SCORE
    # ======================================

    if not scores:

        return 0.0

    return round(
        sum(scores)
        / len(scores),
        2
    )


# ==========================================
# ESG PERFORMANCE STATUS
# ==========================================

def get_esg_status(esg_score):
    """
    Convert ESG score into
    sustainability performance status.
    """

    if esg_score >= 80:

        return "Excellent"

    elif esg_score >= 60:

        return "Good"

    elif esg_score >= 40:

        return "Average"

    return "Needs Improvement"


# ==========================================
# COMPANY ADVANCED ANALYSIS
# ==========================================

def perform_advanced_analysis(
    company_name,
    kpis,
):
    """
    Perform advanced ESG analysis
    for a single company.
    """

    esg_score = calculate_esg_score(
        kpis
    )

    sustainability_status = get_esg_status(
        esg_score
    )

    return {
        "company": company_name,
        "esg_score": esg_score,
        "sustainability_status":
            sustainability_status,
    }


# ==========================================
# COMPANY RANKING
# ==========================================

def rank_companies(all_results):
    """
    Rank companies based on
    their ESG scores.
    """

    ranked_companies = []

    for result in all_results:

        advanced_analysis = result.get(
            "advanced_analysis",
            {}
        )

        ranked_companies.append(
            {
                "company": result.get(
                    "company",
                    "Unknown"
                ),
                "esg_score": advanced_analysis.get(
                    "esg_score",
                    0
                ),
            }
        )

    ranked_companies.sort(
        key=lambda item:
            item["esg_score"],
        reverse=True
    )

    for index, company in enumerate(
        ranked_companies,
        start=1
    ):

        company["rank"] = index

    return ranked_companies