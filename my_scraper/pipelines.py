import psycopg2
from psycopg2.extras import execute_values
from scrapy.exceptions import NotConfigured

from my_scraper.items import OccupationHierarchyItem, OccupationItem, SkillItem


class PostgresPipeline:
    def __init__(self, pg_host, pg_db, pg_user, pg_password):
        self.pg_host = pg_host
        self.pg_db = pg_db
        self.pg_user = pg_user
        self.pg_password = pg_password

    @classmethod
    def from_crawler(cls, crawler):
        pg_host = crawler.settings.get("POSTGRES_HOST")
        pg_db = crawler.settings.get("POSTGRES_DB")
        pg_user = crawler.settings.get("POSTGRES_USER")
        pg_password = crawler.settings.get("POSTGRES_PASSWORD")
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
            self.insert_occupation(item)
            self.insert_relationships(
                item.get("uri"), item.get("essential_skills", []), "essential"
            )
            self.insert_relationships(
                item.get("uri"), item.get("optional_skills", []), "optional"
            )

        elif isinstance(item, SkillItem):
            self.upsert_skill(item, spider)

        elif isinstance(item, OccupationHierarchyItem):
            self.insert_hierarchy(item)

        return item

    # ---------------- insert helpers ----------------

    def insert_occupation(self, item):
        # Determine if it's a leaf node
        is_leaf = not bool(item.get("narrowerConcept"))
        is_functional_leaf = not bool(item.get("narrowerOccupation"))

        query = """
            INSERT INTO occupations (uri, preferred_title, alt_label, description, isco_code,
            broader_isco_group_uri, class_name, is_leaf, is_functional_leaf)
            VALUES %s
            ON CONFLICT (uri) DO NOTHING;
        """
        values = [
            (
                item.get("uri"),
                item.get("preferred_title"),
                item.get("alt_label"),
                item.get("description"),
                item.get("isco_code"),
                item.get("broader_isco_group_uri"),
                item.get("class_name"),
                is_leaf,
                is_functional_leaf,
            )
        ]
        execute_values(self.cursor, query, values)

    def upsert_skill(self, item, spider):
        is_leaf = not bool(item.get("narrowerConcept"))
        is_functional_leaf = not bool(item.get("narrowerSkill"))

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
            item.get("skill_code"),
            item.get("broader_skill_uri"),
            item.get("skill_type"),
            item.get("reuse_level"),
            item.get("scope_note"),
            item.get("class_name"),
            is_leaf,
            is_functional_leaf,
        )

        # Sequential logic: enrichment spider runs later, so we can safely fill in details
        if spider.name == "esco_occupations":
            query = """
            INSERT INTO skills (uri, skill_type, preferred_title)
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
                        is_leaf,
                        is_functional_leaf,
                    )
                ],
            )

        elif spider.name == "esco_skills":
            query = """
                INSERT INTO skills (uri, preferred_title, alt_label, description, skill_code, 
                broader_skill_uri, skill_type, reuse_level, scope_note, class_name,
                is_leaf, is_functional_leaf)

                VALUES %s
                ON CONFLICT (uri)
                DO UPDATE SET
                    preferred_title = COALESCE(EXCLUDED.preferred_title, skills.preferred_title),
                    skill_type      = COALESCE(EXCLUDED.skill_type, skills.skill_type),
                    is_leaf = COALESCE(EXCLUDED.is_leaf, skills.is_leaf),
                    is_functional_leaf = COALESCE(EXCLUDED.is_functional_leaf, skills.is_functional_leaf);
            """
            execute_values(self.cursor, query, [values])

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

    def insert_hierarchy(self, item):
        query = """
            INSERT INTO occupation_hierarchy (parent_uri, child_uri, relation_type)
            VALUES %s
            ON CONFLICT DO NOTHING;
        """
        values = [
            (item.get("parent_uri"), item.get("child_uri"), item.get("relation_type"))
        ]
        execute_values(self.cursor, query, values)
