-- =============================================================================
-- Proskiro core database schema (single source of truth)
-- =============================================================================
--
-- WHY THIS FILE EXISTS
--   Historically the schema for these tables was never defined in one place.
--   Base tables were created by hand, and several columns were bolted on at
--   runtime (e.g. `ALTER TABLE skill_book_matches ADD COLUMN score`, the
--   `free_access_*` columns, `occupations.is_blocked`, `occupations.onet_alt_titles`)
--   or by one-off scripts. That made the real shape of the database implicit and
--   order-dependent. This file documents the full intended schema in one place.
--
-- HOW TO USE IT
--   * Primarily this is REFERENCE / documentation. Normal operation does NOT
--     rebuild from here — crawl jobs update existing rows in place.
--   * It is written to be IDEMPOTENT and SAFE to run against the live database:
--     every statement uses IF NOT EXISTS, so applying it will only *add* any
--     missing tables/columns/indexes and will never drop or alter existing data.
--     Running it is therefore a safe way to reconcile a database that is missing
--     one of the historically runtime-added columns.
--
-- SCOPE
--   Covers the tables written by the `skills` scraper/ETL and read by
--   `proskiro-tools` / the Django site: occupations, skills, occupation_skills,
--   occupation_hierarchy, skill_hierarchy, books, skill_book_matches,
--   book_search_attempts.
-- =============================================================================


-- -----------------------------------------------------------------------------
-- occupations
-- -----------------------------------------------------------------------------
-- Written by: skills/my_scraper/pipelines.py  (upsert_occupation)
-- Read by:    proskiro-tools/data/profession.py
-- Notes:      `slug`, `is_featured` and `email_description` are populated by an
--             external/curation process (not by the scraper), so they are
--             declared defensively here.
CREATE TABLE IF NOT EXISTS occupations (
    uri                     TEXT PRIMARY KEY,
    preferred_title         TEXT,
    alt_label               TEXT,
    description             TEXT,
    email_description       TEXT,
    isco_code               TEXT,
    broader_isco_group_uri  TEXT,
    class_name              TEXT,
    status                  TEXT,
    is_leaf                 BOOLEAN,
    is_functional_leaf      BOOLEAN,
    is_blocked              BOOLEAN NOT NULL DEFAULT FALSE,
    slug                    TEXT,
    is_featured             BOOLEAN DEFAULT FALSE,
    onet_alt_titles         TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Reconcile columns that were historically added at runtime / by one-off scripts.
ALTER TABLE occupations ADD COLUMN IF NOT EXISTS is_blocked        BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE occupations ADD COLUMN IF NOT EXISTS onet_alt_titles   TEXT;
ALTER TABLE occupations ADD COLUMN IF NOT EXISTS slug              TEXT;
ALTER TABLE occupations ADD COLUMN IF NOT EXISTS is_featured       BOOLEAN DEFAULT FALSE;
ALTER TABLE occupations ADD COLUMN IF NOT EXISTS email_description TEXT;

CREATE INDEX IF NOT EXISTS occupations_slug_idx        ON occupations (slug);
CREATE INDEX IF NOT EXISTS occupations_is_featured_idx ON occupations (is_featured);
CREATE INDEX IF NOT EXISTS occupations_isco_code_idx   ON occupations (isco_code);


-- -----------------------------------------------------------------------------
-- skills
-- -----------------------------------------------------------------------------
-- Written by: skills/my_scraper/pipelines.py  (upsert_skill)
-- Read by:    proskiro-tools/data/profession.py
-- Notes:      `book_count` and `occupation_count` are NOT stored here — they are
--             computed at read time from skill_book_matches / occupation_skills.
--             `google_books_total` is updated by search_books_for_skills.py.
CREATE TABLE IF NOT EXISTS skills (
    uri                 TEXT PRIMARY KEY,
    preferred_title     TEXT,
    alt_label           TEXT,
    description         TEXT,
    skill_type          TEXT,
    skill_code          TEXT,
    class_name          TEXT,
    reuse_level         TEXT,
    scope_note          TEXT,
    broader_skill_uri   TEXT,
    is_leaf             BOOLEAN,
    is_functional_leaf  BOOLEAN,
    email_why           TEXT,
    google_books_total  INTEGER,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE skills ADD COLUMN IF NOT EXISTS email_why          TEXT;
ALTER TABLE skills ADD COLUMN IF NOT EXISTS google_books_total INTEGER;

CREATE INDEX IF NOT EXISTS skills_skill_type_idx ON skills (skill_type);


-- -----------------------------------------------------------------------------
-- occupation_skills  (many-to-many: which skills an occupation requires)
-- -----------------------------------------------------------------------------
-- Written by: skills/my_scraper/pipelines.py  (insert_relationships)
-- relation_type is 'essential' or 'optional' (read as skill "importance").
CREATE TABLE IF NOT EXISTS occupation_skills (
    occupation_uri  TEXT NOT NULL,
    skill_uri       TEXT NOT NULL,
    relation_type   TEXT NOT NULL,
    PRIMARY KEY (occupation_uri, skill_uri, relation_type)
);

CREATE INDEX IF NOT EXISTS occupation_skills_skill_uri_idx      ON occupation_skills (skill_uri);
CREATE INDEX IF NOT EXISTS occupation_skills_occupation_uri_idx ON occupation_skills (occupation_uri);


-- -----------------------------------------------------------------------------
-- occupation_hierarchy  (ESCO occupation parent/child tree)
-- -----------------------------------------------------------------------------
-- Written by: skills/my_scraper/pipelines.py  (insert_hierarchy)
CREATE TABLE IF NOT EXISTS occupation_hierarchy (
    parent_uri     TEXT NOT NULL,
    child_uri      TEXT NOT NULL,
    relation_type  TEXT,
    PRIMARY KEY (parent_uri, child_uri, relation_type)
);


-- -----------------------------------------------------------------------------
-- skill_hierarchy  (ESCO skill parent/child tree)
-- -----------------------------------------------------------------------------
-- Written by: skills/my_scraper/pipelines.py  (insert_hierarchy)
CREATE TABLE IF NOT EXISTS skill_hierarchy (
    parent_uri     TEXT NOT NULL,
    child_uri      TEXT NOT NULL,
    relation_type  TEXT,
    PRIMARY KEY (parent_uri, child_uri, relation_type)
);


-- -----------------------------------------------------------------------------
-- books
-- -----------------------------------------------------------------------------
-- Written by: skills/db/db_books.py (save_books) and
--             skills/my_services/book_persistence.py (upsert_book);
--             amazon_affiliate_url set by skills/my_services/affiliate_links.py.
-- Read by:    proskiro-tools/data/profession.py (thumbnail exposed as cover_url).
CREATE TABLE IF NOT EXISTS books (
    id                   SERIAL PRIMARY KEY,
    source               TEXT,
    external_id          TEXT,
    isbn_10              TEXT,
    isbn_13              TEXT,
    title                TEXT,
    authors              TEXT[],
    description          TEXT,
    subjects             TEXT[],
    language_code        TEXT,
    published_year       INTEGER,
    average_rating       DOUBLE PRECISION,
    ratings_count        INTEGER,
    metadata             JSONB,
    thumbnail            TEXT,
    free_access_type     TEXT,
    free_access_url      TEXT,
    free_access_source   TEXT,
    free_access_epub     BOOLEAN DEFAULT FALSE,
    free_access_pdf      BOOLEAN DEFAULT FALSE,
    amazon_affiliate_url TEXT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source, external_id)
);

-- Reconcile columns historically written only "if the column happened to exist".
ALTER TABLE books ADD COLUMN IF NOT EXISTS free_access_type     TEXT;
ALTER TABLE books ADD COLUMN IF NOT EXISTS free_access_url      TEXT;
ALTER TABLE books ADD COLUMN IF NOT EXISTS free_access_source   TEXT;
ALTER TABLE books ADD COLUMN IF NOT EXISTS free_access_epub     BOOLEAN DEFAULT FALSE;
ALTER TABLE books ADD COLUMN IF NOT EXISTS free_access_pdf      BOOLEAN DEFAULT FALSE;
ALTER TABLE books ADD COLUMN IF NOT EXISTS amazon_affiliate_url TEXT;

CREATE INDEX IF NOT EXISTS books_isbn_10_idx ON books (isbn_10);
CREATE INDEX IF NOT EXISTS books_isbn_13_idx ON books (isbn_13);


-- -----------------------------------------------------------------------------
-- skill_book_matches  (which books are recommended for a skill/occupation pair)
-- -----------------------------------------------------------------------------
-- Written by: skills/my_services/book_persistence.py (link_book_to_skill).
-- `score` was historically added at runtime via ALTER TABLE.
CREATE TABLE IF NOT EXISTS skill_book_matches (
    skill_uri       TEXT NOT NULL,
    occupation_uri  TEXT NOT NULL,
    book_id         INTEGER NOT NULL REFERENCES books (id) ON DELETE CASCADE,
    rank            INTEGER,
    fallback_tier   INTEGER DEFAULT 0,
    score           DOUBLE PRECISION DEFAULT 0.0,
    matched_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (skill_uri, occupation_uri, book_id)
);

ALTER TABLE skill_book_matches ADD COLUMN IF NOT EXISTS score DOUBLE PRECISION DEFAULT 0.0;

CREATE INDEX IF NOT EXISTS skill_book_matches_skill_occ_idx
    ON skill_book_matches (skill_uri, occupation_uri);


-- -----------------------------------------------------------------------------
-- book_search_attempts  (idempotency log: which searches have been tried)
-- -----------------------------------------------------------------------------
-- Written by: skills/my_services/search_books_for_skills.py (record_search_attempt).
CREATE TABLE IF NOT EXISTS book_search_attempts (
    skill_uri       TEXT NOT NULL,
    occupation_uri  TEXT NOT NULL,
    source          TEXT NOT NULL,
    searched_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    books_found     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (skill_uri, occupation_uri, source)
);
