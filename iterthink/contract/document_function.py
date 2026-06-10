"""Document function IDs from pragmatic-bim-data-contract v0.1.0."""

from __future__ import annotations

# Top-level + leaf IDs used for Impact context scoping (document-function.skos.ttl).
REGULATORY_NORMATIVE = "regulatory_normative"
REG_NORMS = "reg_norms"
REG_LAWS = "reg_laws"
REG_STANDARDS = "reg_standards"

TECHNICAL = "technical"
TEC_DOCUMENTS = "tec_documents"
TEC_PLANS = "tec_plans"
TEC_SCHEMATICS = "tec_schematics"
TEC_MANUALS = "tec_manuals"

REQUIREMENTS_BRIEFS = "requirements_briefs"
REQ_PROJECT_BRIEFS = "req_project_briefs"
REQ_FUNCTIONAL_SPECIFICATIONS = "req_functional_specifications"
REQ_TENDER_DOCUMENTS = "req_tender_documents"

# Impact check → document functions that supply relevant RAG context.
IMPACT_CHECK_CONTEXT_FUNCTIONS: dict[str, frozenset[str]] = {
    "norm_compliance": frozenset(
        {
            REGULATORY_NORMATIVE,
            REG_NORMS,
            REG_LAWS,
            REG_STANDARDS,
            "reg_compliance_reports",
        }
    ),
    "impact_consistency": frozenset(
        {
            TECHNICAL,
            TEC_DOCUMENTS,
            TEC_PLANS,
            TEC_SCHEMATICS,
            TEC_MANUALS,
            REQUIREMENTS_BRIEFS,
            REQ_PROJECT_BRIEFS,
            REQ_FUNCTIONAL_SPECIFICATIONS,
            REQ_TENDER_DOCUMENTS,
        }
    ),
    "scope_completeness": frozenset(
        {
            TECHNICAL,
            TEC_DOCUMENTS,
            REQUIREMENTS_BRIEFS,
            REQ_FUNCTIONAL_SPECIFICATIONS,
            REQ_TENDER_DOCUMENTS,
        }
    ),
    "risk_assessment": frozenset(
        {
            TECHNICAL,
            TEC_DOCUMENTS,
            TEC_PLANS,
            REQUIREMENTS_BRIEFS,
            REQ_FUNCTIONAL_SPECIFICATIONS,
            REQ_TENDER_DOCUMENTS,
            REQ_PROJECT_BRIEFS,
            "legal_contractual",
            "leg_contracts",
        }
    ),
    "design_intent": frozenset(
        {
            REQUIREMENTS_BRIEFS,
            REQ_PROJECT_BRIEFS,
            "mks_case_studies",
            TEC_DOCUMENTS,
        }
    ),
}
