"""
Analyze fallback tier usage across skill-book matches.

Helps understand:
- Which skills consistently need fallbacks
- Distribution of fallback strategies
- Quality differences between tiers
"""

from my_tools.db import get_db_connection


def analyze_fallback_distribution():
    """Show distribution of books by fallback tier."""
    conn = get_db_connection()

    sql = """
    SELECT
        fallback_tier,
        COUNT(*) as book_count,
        COUNT(DISTINCT skill_uri) as skill_count,
        ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER (), 2) as percentage
    FROM skill_book_matches
    WHERE fallback_tier IS NOT NULL
    GROUP BY fallback_tier
    ORDER BY fallback_tier;
    """

    with conn.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()

    print("\n=== Fallback Tier Distribution ===")
    print(f"{'Tier':<6} {'Strategy':<25} {'Books':<10} {'Skills':<10} {'%':<8}")
    print("-" * 65)

    tier_names = {
        0: "Primary (no fallback)",
        1: "Occupation fallback",
        2: "Year fallback",
        3: "Broader skill",
        4: "Broader + year",
    }

    for tier, book_count, skill_count, pct in rows:
        tier_name = tier_names.get(tier, "Unknown")
        print(f"{tier:<6} {tier_name:<25} {book_count:<10} {skill_count:<10} {pct:<8}%")

    conn.close()


def top_fallback_skills(tier=None, limit=20):
    """Show skills that most frequently use fallbacks."""
    conn = get_db_connection()

    tier_filter = f"AND sbm.fallback_tier = {tier}" if tier is not None else "AND sbm.fallback_tier > 0"

    sql = f"""
    SELECT
        s.preferred_title,
        o.preferred_title as occupation,
        sbm.fallback_tier,
        COUNT(*) as book_count,
        b.source
    FROM skill_book_matches sbm
    JOIN skills s ON s.uri = sbm.skill_uri
    JOIN occupations o ON o.uri = sbm.occupation_uri
    JOIN books b ON b.id = sbm.book_id
    WHERE 1=1 {tier_filter}
    GROUP BY s.preferred_title, o.preferred_title, sbm.fallback_tier, b.source
    ORDER BY sbm.fallback_tier DESC, book_count DESC
    LIMIT %s;
    """

    with conn.cursor() as cur:
        cur.execute(sql, (limit,))
        rows = cur.fetchall()

    tier_names = {
        1: "Occupation fallback",
        2: "Year fallback",
        3: "Broader skill",
        4: "Broader + year",
    }

    tier_desc = f" (Tier {tier}: {tier_names.get(tier, 'Unknown')})" if tier is not None else ""
    print(f"\n=== Top Skills Using Fallbacks{tier_desc} ===")
    print(f"{'Skill':<40} {'Occupation':<30} {'Tier':<6} {'Books':<8} {'Source':<15}")
    print("-" * 105)

    for skill, occupation, fb_tier, book_count, source in rows:
        print(f"{skill[:39]:<40} {occupation[:29]:<30} {fb_tier:<6} {book_count:<8} {source:<15}")

    conn.close()


def compare_sources_by_tier():
    """Compare Google Books vs Open Library fallback usage."""
    conn = get_db_connection()

    sql = """
    SELECT
        b.source,
        sbm.fallback_tier,
        COUNT(*) as book_count
    FROM skill_book_matches sbm
    JOIN books b ON b.id = sbm.book_id
    WHERE sbm.fallback_tier IS NOT NULL
    GROUP BY b.source, sbm.fallback_tier
    ORDER BY b.source, sbm.fallback_tier;
    """

    with conn.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()

    print("\n=== Fallback Usage by Source ===")

    # Group by source
    by_source = {}
    for source, tier, count in rows:
        if source not in by_source:
            by_source[source] = {}
        by_source[source][tier] = count

    tier_names = {
        0: "Primary",
        1: "Occup",
        2: "Year",
        3: "Broader",
        4: "Broad+Yr",
    }

    print(f"{'Source':<20} ", end="")
    for tier in sorted(tier_names.keys()):
        print(f"{tier_names[tier]:<12}", end="")
    print()
    print("-" * 80)

    for source, tiers in by_source.items():
        print(f"{source:<20} ", end="")
        for tier in sorted(tier_names.keys()):
            count = tiers.get(tier, 0)
            print(f"{count:<12}", end="")
        print()

    conn.close()


def skills_needing_attention(min_tier=3):
    """Find skills that consistently require high-tier fallbacks."""
    conn = get_db_connection()

    sql = """
    SELECT
        s.preferred_title,
        s.description,
        o.preferred_title as occupation,
        AVG(sbm.fallback_tier) as avg_tier,
        COUNT(*) as book_count
    FROM skill_book_matches sbm
    JOIN skills s ON s.uri = sbm.skill_uri
    JOIN occupations o ON o.uri = sbm.occupation_uri
    WHERE sbm.fallback_tier >= %s
    GROUP BY s.preferred_title, s.description, o.preferred_title
    HAVING COUNT(*) >= 3  -- At least 3 books from high-tier fallbacks
    ORDER BY avg_tier DESC, book_count DESC
    LIMIT 30;
    """

    with conn.cursor() as cur:
        cur.execute(sql, (min_tier,))
        rows = cur.fetchall()

    print(f"\n=== Skills Needing Attention (Avg Tier >= {min_tier}) ===")
    print("These skills might benefit from better query expansions or skill descriptions.\n")
    print(f"{'Skill':<40} {'Occupation':<30} {'Avg Tier':<10} {'Books':<8}")
    print("-" * 95)

    for skill, desc, occupation, avg_tier, book_count in rows:
        print(f"{skill[:39]:<40} {occupation[:29]:<30} {avg_tier:<10.2f} {book_count:<8}")

    conn.close()


if __name__ == "__main__":
    print("=" * 80)
    print("FALLBACK TIER ANALYSIS")
    print("=" * 80)

    analyze_fallback_distribution()
    compare_sources_by_tier()
    top_fallback_skills(limit=15)
    skills_needing_attention()

    print("\n" + "=" * 80)