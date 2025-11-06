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
                    self.insert_skill(skill)
            self.insert_occupation(item)
            self.insert_relationships(
                item.get("uri"), item.get("essential_skills", []), "essential"
            )
            self.insert_relationships(
                item.get("uri"), item.get("optional_skills", []), "optional"
            )

        elif isinstance(item, SkillItem):
            self.insert_skill(item)

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

    def insert_skill(self, item):
        query = """
            INSERT INTO skills (uri, skill_type, preferred_title)
            VALUES %s
            ON CONFLICT (uri) DO NOTHING;
        """
        values = [
            (item.get("uri"), item.get("skill_type"), item.get("preferred_title"))
        ]
        execute_values(self.cursor, query, values)

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
