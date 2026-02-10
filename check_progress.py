#!/usr/bin/env python
"""Check progress of skill-occupation book searches."""
from my_tools.db import get_db_connection

conn = get_db_connection()
cur = conn.cursor()

# Unique knowledge skills
cur.execute("SELECT COUNT(DISTINCT uri) FROM skills WHERE skill_type ILIKE 'knowledge' AND description IS NOT NULL AND is_leaf = TRUE")
unique_skills = cur.fetchone()[0]
print(f"Unique knowledge skills: {unique_skills}")

# Unique occupations
cur.execute("SELECT COUNT(DISTINCT uri) FROM occupations")
unique_occs = cur.fetchone()[0]
print(f"Unique occupations: {unique_occs}")

# Total skill-occupation pairs (knowledge skills only)
cur.execute("""
    SELECT COUNT(DISTINCT (s.uri, os.occupation_uri)) 
    FROM skills s 
    JOIN occupation_skills os ON s.uri = os.skill_uri 
    JOIN occupations o ON os.occupation_uri = o.uri 
    WHERE s.skill_type ILIKE 'knowledge' 
    AND s.description IS NOT NULL 
    AND s.is_leaf = TRUE
""")
total = cur.fetchone()[0]
print(f"Total skill-occupation pairs to search: {total}")

# Example: how many occupations need 'communication'?
cur.execute("""
    SELECT COUNT(DISTINCT os.occupation_uri) 
    FROM skills s 
    JOIN occupation_skills os ON s.uri = os.skill_uri 
    WHERE s.preferred_title ILIKE 'communication'
""")
comm_occs = cur.fetchone()[0]
print(f"Example - occupations needing 'communication': {comm_occs}")

print()

# Records with proper occupation_uri
cur.execute("SELECT COUNT(*) FROM skill_book_matches WHERE occupation_uri IS NOT NULL")
proper_count = cur.fetchone()[0]

# Unique skill-occupation pairs already searched
cur.execute("SELECT COUNT(DISTINCT (skill_uri, occupation_uri)) FROM skill_book_matches WHERE occupation_uri IS NOT NULL")
searched = cur.fetchone()[0]

print(f"Book records saved: {proper_count}")
print(f"Unique skill-occupation pairs searched: {searched}")
print(f"Remaining to search: {total - searched}")

conn.close()
