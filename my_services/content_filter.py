"""
Content Filter Configuration

Defines occupations and skills to exclude from the platform.
Used by search_books_for_skills.py and other services.

Filtering is done via:
1. Exact URI matches (most precise)
2. Keyword matches in titles/descriptions (broader filtering)
"""

# Occupation URIs to exclude (exact match)
# Find URIs at: https://ec.europa.eu/esco/portal/occupation
EXCLUDED_OCCUPATION_URIS = {
    # Example: "http://data.europa.eu/esco/occupation/..."
}

# Skill URIs to exclude (exact match)
# Find URIs at: https://ec.europa.eu/esco/portal/skill
EXCLUDED_SKILL_URIS = {
    # Example: "http://data.europa.eu/esco/skill/..."
}

# Keywords in occupation titles to exclude (case-insensitive)
EXCLUDED_OCCUPATION_KEYWORDS = {
    # Alcohol-related
    "bartender",
    "sommelier",
    "brewer",
    "distiller",
    "winemaker",
    "wine",
    "brewery",
    "distillery",
    "bar manager",
    "mixologist",
    # Gambling/Casino-related
    "casino",
    "gambling",
    "croupier",
    "bookmaker",
    "betting",
    "poker",
    "slot machine",
    "gaming manager",
    "gaming supervisor",
    "gaming dealer",
    "gaming attendant",
    "gaming inspector",
    "odds compiler",
    "pit boss",
    "lottery",
    # Pork/non-halal food specific roles
    "pork",
    "pig farm",
    "swine",
    "piggery",
    # Adult entertainment
    "adult entertainment",
    "nightclub",
    "strip club",
    "stripper",
    "exotic dancer",
    "adult film",
    "pornograph",
    # Tobacco
    "tobacco",
    "cigarette",
    "cigar",
    # Fortune-telling / Occult
    "fortune tell",
    "psychic",
    "astrologer",
    "astrology",
    "tarot",
    "clairvoyant",
    "divination",
    "occult",
    "spiritualist",
    # Insurance (interest-based conventional insurance)
    "insurance",
    "actuary",
    "actuarial",
    "claims adjuster",
    # Music/Entertainment
    "musician",
    "singer",
    "vocalist",
    "music producer",
    "music director",
    "composer",
    "songwriter",
    "disc jockey",
    "dj",
    "sound engineer",
    "recording artist",
    "music teacher",
    "music therapist",
    "orchestra",
    "orchestra conductor",
    "instrumentalist",
    # Interest-based finance
    "usury",
    "interest rate",
    "loan officer",
    "debt collector",
    "payday loan",
    "mortgage",
    "credit",
}

# Keywords in skill titles/descriptions to exclude (case-insensitive)
EXCLUDED_SKILL_KEYWORDS = {
    # Alcohol-related skills
    "alcoholic beverage",
    "wine tasting",
    "wine pairing",
    "beer brewing",
    "distillation of spirits",
    "cocktail mixing",
    "bartending",
    "sommelier",
    # Gambling/Casino-related skills
    "gambling",
    "casino games",
    "casino operations",
    "betting odds",
    "odds compiler",
    "gaming inspector",
    "poker",
    "gaming tables",
    "slot machines",
    "roulette",
    "blackjack",
    "baccarat",
    "gaming regulations",
    "lottery",
    # Pork-related
    "pork processing",
    "pork butchery",
    "pig farming",
    "swine husbandry",
    # Insurance skills (conventional interest-based)
    "insurance policy",
    "insurance claim",
    "insurance premium",
    "underwriting",
    "actuarial",
    "risk assessment for insurance",
    "insurance products",
    "life insurance",
    "health insurance",
    "property insurance",
    "liability insurance",
    # Tobacco-related skills
    "tobacco processing",
    "cigarette manufacturing",
    # Fortune-telling / Occult skills
    "fortune telling",
    "astrology",
    "tarot reading",
    "divination",
    "occult",
    "psychic",
    "clairvoyant",
    "spiritualist",
    # Adult entertainment skills
    "striptease",
    "adult entertainment",
    "nightclub",
    "pornograph",
    "exotic danc",
    # Interest-based finance skills
    "usury",
    "interest rate",
    "mortgage",
    "credit",
    "debt collection",
    "payday lending",
    # Music-related skills
    "musical instrument",
    "music theory",
    "music composition",
    "music production",
    "singing",
    "vocal",
    "music performance",
    "music notation",
    "audio mixing",
    "sound recording",
    "music education",
    "orchestration",
    "conducting music",
    "songwriting",
    "music",
}


def is_occupation_excluded(occupation_uri: str, occupation_title: str) -> bool:
    """
    Check if an occupation should be excluded.
    
    Args:
        occupation_uri: The ESCO occupation URI
        occupation_title: The occupation title
        
    Returns:
        True if occupation should be excluded
    """
    # Check exact URI match
    if occupation_uri in EXCLUDED_OCCUPATION_URIS:
        return True
    
    # Check keyword match in title
    if occupation_title:
        title_lower = occupation_title.lower()
        for keyword in EXCLUDED_OCCUPATION_KEYWORDS:
            if keyword in title_lower:
                return True
    
    return False


def is_skill_excluded(
    skill_uri: str, 
    skill_title: str, 
    skill_description: str = None
) -> bool:
    """
    Check if a skill should be excluded.
    
    Args:
        skill_uri: The ESCO skill URI
        skill_title: The skill title
        skill_description: Optional skill description
        
    Returns:
        True if skill should be excluded
    """
    # Check exact URI match
    if skill_uri in EXCLUDED_SKILL_URIS:
        return True
    
    # Check keyword match in title
    if skill_title:
        title_lower = skill_title.lower()
        for keyword in EXCLUDED_SKILL_KEYWORDS:
            if keyword in title_lower:
                return True
    
    # Check keyword match in description
    if skill_description:
        desc_lower = skill_description.lower()
        for keyword in EXCLUDED_SKILL_KEYWORDS:
            if keyword in desc_lower:
                return True
    
    return False
