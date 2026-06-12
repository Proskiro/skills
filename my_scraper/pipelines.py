import os

import psycopg2
from psycopg2.extras import execute_values
from scrapy.exceptions import NotConfigured

from my_scraper.items import (
    OccupationHierarchyItem,
    OccupationItem,
    SkillHierarchyItem,
    SkillItem,
)
from my_services.content_filter import is_occupation_excluded


class PostgresPipeline:
    def __init__(self, pg_host, pg_db, pg_user, pg_password):
        self.pg_host = pg_host
        self.pg_db = pg_db
        self.pg_user = pg_user
        self.pg_password = pg_password

    @classmethod
    def from_crawler(cls, crawler):
        pg_host = os.getenv("POSTGRES_HOST")
        pg_db = os.getenv("POSTGRES_DB")
        pg_user = os.getenv("POSTGRES_USER")
        pg_password = os.getenv("POSTGRES_PASSWORD")
        # If any required variable is missing
        if not all([pg_host, pg_db, pg_user, pg_password]):
            raise NotConfigured("Postgres credentials missing")
        return cls(pg_host, pg_db, pg_user, pg_password)

    def open_spider(self, spider):
        self.conn = psycopg2.connect(
            host=self.pg_host,
            dbname=self.pg_db,
            user=self.pg_user,
            password=self.pg_password,
        )
        self.cursor = self.conn.cursor()

    def close_spider(self, spider):
        self.conn.commit()
        self.cursor.close()
        self.conn.close()

    def process_item(self, item, spider):
        if isinstance(item, OccupationItem):
            for skill_list_name in ("essential_skills", "optional_skills"):
                for skill in item.get(skill_list_name, []):
                    self.upsert_skill(skill, spider)
            self.upsert_occupation(item)
            self.insert_relationships(
                item.get("uri"), item.get("essential_skills", []), "essential"
            )
            self.insert_relationships(
                item.get("uri"), item.get("optional_skills", []), "optional"
            )

        elif isinstance(item, SkillItem):
            self.upsert_skill(item, spider)

        elif isinstance(item, OccupationHierarchyItem):
            self.insert_hierarchy(item, spider)

        elif isinstance(item, SkillHierarchyItem):
            self.insert_hierarchy(item, spider)

        return item

    # ---------------- insert helpers ----------------

    def upsert_occupation(self, item):
        blocked = is_occupation_excluded(
            item.get("uri", ""), item.get("preferred_title", "")
        )

        query = """
            INSERT INTO occupations (
                uri,
                preferred_title,
                alt_label,
                description,
                isco_code,
                broader_isco_group_uri,
                status,
                is_leaf,
                is_functional_leaf,
                is_blocked,
                created_at,
                updated_at
            )
            VALUES %s
            ON CONFLICT (uri)
            DO UPDATE SET
                preferred_title          = EXCLUDED.preferred_title,
                alt_label                = EXCLUDED.alt_label,
                description              = EXCLUDED.description,
                isco_code                = EXCLUDED.isco_code,
                broader_isco_group_uri   = EXCLUDED.broader_isco_group_uri,
                status                   = EXCLUDED.status,
                is_leaf                  = EXCLUDED.is_leaf,
                is_functional_leaf       = EXCLUDED.is_functional_leaf,
                is_blocked               = EXCLUDED.is_blocked,
                updated_at               = now();
        """

        values = [
            (
                item.get("uri"),
                item.get("preferred_title"),
                item.get("alt_label"),
                item.get("description"),
                item.get("isco_code"),
                item.get("broader_isco_group_uri"),
                item.get("status"),
                item.get("is_leaf"),
                item.get("is_functional_leaf"),
                blocked,
                None,  # created_at → DB default
                None,  # updated_at → handled by now()
            )
        ]

        execute_values(self.cursor, query, values)

    def upsert_skill(self, item, spider):
        """
        Insert or enrich a skill record.
        - If called from the occupations spider: insert minimal info.
        - If called from the skills spider: enrich / fill in missing fields.
        """

        # Extract all possible fields
        values = (
            item.get("uri"),
            item.get("preferred_title"),
            item.get("alt_label"),
            item.get("description"),
            item.get("skill_type"),
            item.get("skill_code"),
            item.get("class_name"),
            item.get("reuse_level"),
            item.get("scope_note"),
            item.get("broader_skill_uri"),
            item.get("is_leaf"),
            item.get("is_functional_leaf"),
        )

        # Sequential logic: enrichment spider runs later, so we can safely fill in details
        if spider.name == "esco_occupations":
            query = """
            INSERT INTO skills (uri, skill_type, preferred_title, is_leaf, is_functional_leaf)
            VALUES %s
            ON CONFLICT (uri) DO NOTHING;
            """
            execute_values(
                self.cursor,
                query,
                [
                    (
                        item.get("uri"),
                        item.get("skill_type"),
                        item.get("preferred_title"),
                        item.get("is_leaf"),
                        item.get("is_functional_leaf"),
                    )
                ],
            )

        elif spider.name == "esco_skills":
            try:
                query = """
                INSERT INTO skills (
                    uri, preferred_title, alt_label, description, skill_type,
                    skill_code, class_name, reuse_level, scope_note, broader_skill_uri,
                    is_leaf, is_functional_leaf
                )
                VALUES %s
                ON CONFLICT (uri)
                DO UPDATE SET
                    preferred_title       = COALESCE(EXCLUDED.preferred_title, skills.preferred_title),
                    alt_label             = COALESCE(EXCLUDED.alt_label, skills.alt_label),
                    description           = COALESCE(EXCLUDED.description, skills.description),
                    skill_type            = COALESCE(EXCLUDED.skill_type, skills.skill_type),
                    skill_code            = COALESCE(EXCLUDED.skill_code, skills.skill_code),
                    class_name            = COALESCE(EXCLUDED.class_name, skills.class_name),
                    reuse_level           = COALESCE(EXCLUDED.reuse_level, skills.reuse_level),
                    scope_note            = COALESCE(EXCLUDED.scope_note, skills.scope_note),
                    broader_skill_uri     = COALESCE(EXCLUDED.broader_skill_uri, skills.broader_skill_uri),
                    is_leaf               = EXCLUDED.is_leaf,
                    is_functional_leaf    = EXCLUDED.is_functional_leaf,
                    updated_at            = now();
                """
                execute_values(self.cursor, query, [values])
            except Exception as e:
                spider.logger.error("\n\n🚨 DB ERROR (REAL CAUSE BELOW) 🚨\n")
                spider.logger.error(f"URI: {item.get('uri')}")
                spider.logger.error(f"Values: {values}")
                spider.logger.error(f"Error type: {type(e)}")
                spider.logger.error(f"Error message: {e}\n")
                self.conn.rollback()

    def insert_relationships(self, occupation_uri, skills, rel_type):
        query = """
            INSERT INTO occupation_skills (occupation_uri, skill_uri, relation_type)
            VALUES %s
            ON CONFLICT DO NOTHING;
        """
        values = [
            (occupation_uri, s.get("uri"), rel_type) for s in skills if s.get("uri")
        ]
        if values:
            execute_values(self.cursor, query, values)

    def insert_hierarchy(self, item, spider):
        parent = item.get("parent_uri")
        child = item.get("child_uri")
        relation = item.get("relation_type")

        if spider.name == "esco_occupations":
            query = """
                INSERT INTO occupation_hierarchy (parent_uri, child_uri, relation_type)
                VALUES %s
                ON CONFLICT DO NOTHING;
            """
        elif spider.name == "esco_skills":
            query = """
                INSERT INTO skill_hierarchy (parent_uri, child_uri, relation_type)
                VALUES %s
                ON CONFLICT DO NOTHING;
            """
        else:
            return item  # just in case

        values = [(parent, child, relation)]
        execute_values(self.cursor, query, values)
