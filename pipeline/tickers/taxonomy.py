#!/usr/bin/env python3
"""
Sector taxonomy — parent/child relationships and normalization map
Normalizes inconsistent sector names and enables hierarchical queries
"""

# ─── Taxonomy Definition ──────────────────────────────────────────────────────

TAXONOMY = {
    "technology": {
        "children": [
            "ai", "ai_infrastructure", "semiconductors", "memory",
            "data_center", "cybersecurity", "software", "hardware",
            "cloud", "fintech", "payments", "communications",
            "telecommunications", "gaming", "social_media",
        ]
    },
    "energy": {
        "children": [
            "oil_gas", "utilities", "renewables", "nuclear", "lng",
            "pipelines", "power_infrastructure",
        ]
    },
    "financials": {
        "children": [
            "banking", "insurance", "real_estate", "fintech",
            "institutional", "financial_services", "payments",
            "asset_management",
        ]
    },
    "healthcare": {
        "children": [
            "biotech", "pharma", "medical_devices", "health_insurance",
            "biotechnology", "pharmaceuticals", "diagnostics",
        ]
    },
    "industrials": {
        "children": [
            "manufacturing", "defense", "aerospace", "construction",
            "transportation", "aviation", "airlines", "infrastructure",
            "industrial", "steel", "chemicals", "materials",
        ]
    },
    "consumer": {
        "children": [
            "retail", "consumer_staples", "consumer_discretionary",
            "automotive", "ev", "travel", "food", "grocery",
            "apparel", "cannabis",
        ]
    },
    "materials": {
        "children": [
            "chemicals", "mining", "agriculture", "commodities",
            "steel", "neon_gas", "specialty_gases",
        ]
    },
    "macro": {
        "children": [
            "geopolitical", "trade", "forex", "interest_rates",
            "inflation", "monetary_policy", "fiscal_policy",
            "employment", "gdp",
        ]
    },
    "emerging_markets": {
        "children": [
            "china", "japan", "europe", "uk_equities", "gbp",
            "asia_pacific", "latin_america",
        ]
    },
    "equity_risk": {
        "children": [
            "small_cap", "large_cap", "insider_trading",
            "corporate_governance", "ipo", "spac",
        ]
    },
}

# ─── Normalization Map ─────────────────────────────────────────────────────────
# Maps inconsistent/legacy names → canonical names

NORMALIZE = {
    # Spacing issues
    "real estate":          "real_estate",
    "oil gas":              "oil_gas",
    "ai infrastructure":    "ai_infrastructure",
    "data center":          "data_center",
    "consumer staples":     "consumer_staples",
    "financial services":   "financial_services",
    "medical devices":      "medical_devices",

    # Plural/singular inconsistencies
    "industrial":           "industrials",
    "pharmaceutical":       "pharma",
    "pharmaceuticals":      "pharma",
    "biotechnology":        "biotech",
    "telecommunication":    "telecommunications",
    "semiconductor":        "semiconductors",

    # Aliases
    "oil_and_gas":          "oil_gas",
    "finservices":          "financial_services",
    "ev_vehicles":          "ev",
    "electric_vehicle":     "ev",
    "electric_vehicles":    "ev",
    "renewable":            "renewables",
    "renewable_energy":     "renewables",
    "clean_energy":         "renewables",
    "defense_aerospace":    "defense",
    "govt":                 "geopolitical",
    "government":           "geopolitical",
    "politics":             "geopolitical",
    "macro_economics":      "macro",
    "macroeconomic":        "macro",
    "commodity":            "commodities",
    "raw_materials":        "materials",
    "banks":                "banking",
    "bank":                 "banking",
    "insurer":              "insurance",
    "insurers":             "insurance",
    "realestate":           "real_estate",
    "property":             "real_estate",
    "chip":                 "semiconductors",
    "chips":                "semiconductors",
    "gpu":                  "semiconductors",
    "hbm_memory":           "memory",
    "dram":                 "memory",
    "neon_gas":             "materials",
    "specialty_gases":      "materials",
    "lng":                  "energy",
    "natural_gas":          "oil_gas",
    "crude_oil":            "oil_gas",
    "power":                "utilities",
    "power_grid":           "utilities",
    "grid":                 "utilities",
    "internet":             "technology",
    "ecommerce":            "retail",
    "e_commerce":           "retail",
    "logistics":            "transportation",
    "shipping":             "transportation",
    "freight":              "transportation",
    "payments":             "fintech",
    "payment":              "fintech",
    "crypto":               "fintech",
    "cryptocurrency":       "fintech",
    "blockchain":           "fintech",
    "gbp":                  "emerging_markets",
    "uk_equities":          "emerging_markets",
    "japan":                "emerging_markets",
    "china":                "emerging_markets",
    "forex":                "macro",
    "fx":                   "macro",
    "currency":             "macro",
    "interest_rates":       "macro",
    "inflation":            "macro",
    "gdp":                  "macro",
    "employment":           "macro",
    "trade":                "macro",
    "exporters":            "macro",
    "institutional":        "financials",
    "insider_trading":      "equity_risk",
    "small_cap":            "equity_risk",
    "corporate_governance": "equity_risk",
}

# ─── Build reverse lookup ──────────────────────────────────────────────────────

# child → parent mapping
CHILD_TO_PARENT = {}
for parent, data in TAXONOMY.items():
    for child in data.get("children", []):
        CHILD_TO_PARENT[child] = parent

# All valid canonical sector names
VALID_SECTORS = set(TAXONOMY.keys()) | set(CHILD_TO_PARENT.keys())


def normalize_sector(sector: str) -> str:
    """Normalize a sector name to its canonical form"""
    s = sector.lower().strip().replace(" ", "_").replace("-", "_")
    return NORMALIZE.get(s, s)


def get_parent(sector: str) -> str | None:
    """Get the parent sector for a child sector"""
    normalized = normalize_sector(sector)
    return CHILD_TO_PARENT.get(normalized)


def get_children(sector: str) -> list[str]:
    """Get all child sectors for a parent"""
    normalized = normalize_sector(sector)
    return TAXONOMY.get(normalized, {}).get("children", [])


def expand_sectors(sectors: list[str]) -> list[str]:
    """
    Expand a list of sectors to include parent sectors.
    e.g. ["ai_infrastructure"] → ["ai_infrastructure", "technology"]
    """
    expanded = set()
    for sector in sectors:
        normalized = normalize_sector(sector)
        expanded.add(normalized)
        parent = CHILD_TO_PARENT.get(normalized)
        if parent:
            expanded.add(parent)
    return sorted(expanded)


def normalize_sectors(sectors: list[str]) -> list[str]:
    """Normalize and deduplicate a list of sector names"""
    normalized = set()
    for sector in sectors:
        n = normalize_sector(sector)
        normalized.add(n)
        # Also add parent for completeness
        parent = CHILD_TO_PARENT.get(n)
        if parent:
            normalized.add(parent)
    return sorted(normalized)


def sectors_for_query(query_sectors: list[str]) -> list[str]:
    """
    For a Qdrant query, expand parent sectors to include all children.
    e.g. ["technology"] → ["technology", "ai", "ai_infrastructure", ...]
    """
    all_sectors = set()
    for sector in query_sectors:
        normalized = normalize_sector(sector)
        all_sectors.add(normalized)
        # Add all children if this is a parent
        children = get_children(normalized)
        all_sectors.update(children)
        # Add parent if this is a child
        parent = get_parent(normalized)
        if parent:
            all_sectors.add(parent)
    return sorted(all_sectors)


if __name__ == "__main__":
    # Test normalization
    test_sectors = [
        "real estate", "biotechnology", "pharmaceuticals", "industrial",
        "ai_infrastructure", "neon_gas", "gbp", "payments", "chips",
        "renewable_energy", "natural_gas", "insider_trading",
    ]

    print("Normalization tests:")
    for s in test_sectors:
        normalized = normalize_sector(s)
        parent     = get_parent(normalized)
        print(f"  {s:25s} → {normalized:25s} (parent: {parent})")

    print("\nExpansion tests:")
    test_lists = [
        ["ai_infrastructure", "semiconductors"],
        ["real estate", "oil_gas"],
        ["biotechnology", "pharmaceuticals"],
    ]
    for sectors in test_lists:
        expanded = normalize_sectors(sectors)
        print(f"  {sectors} → {expanded}")

    print("\nQuery expansion (technology):")
    print(f"  {sectors_for_query(['technology'])}")

    print("\nQuery expansion (energy):")
    print(f"  {sectors_for_query(['energy'])}")
