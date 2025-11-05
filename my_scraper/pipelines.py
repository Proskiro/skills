import psycopg2
# from psycopg2.extras import execute_values
from scrapy.exceptions import NotConfigured


class PostgresPipeline:
    """
    Pipeline to insert scraped items into PostgreSQL.
    """

    def __init__(self, pg_host, pg_db, pg_user, pg_password):
        self.pg_host = pg_host
        self.pg_db = pg_db
        self.pg_user = pg_user
        self.pg_password = pg_password

    @classmethod
    def from_crawler(cls, crawler):
        # Read settings from settings.py
        pg_host = crawler.settings.get("POSTGRES_HOST")
        pg_db = crawler.settings.get("POSTGRES_DB")
        pg_user = crawler.settings.get("POSTGRES_USER")
        pg_password = crawler.settings.get("POSTGRES_PASSWORD")

        if not all([pg_host, pg_db, pg_user, pg_password]):
            raise NotConfigured("Postgres credentials missing in settings.py")

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

#     def process_item(self, item, spider):
#         # Determine which table to insert into based on item type
#         if item.__class__.__name__ == "SkillItem":
#             self.insert_skill(item)
#         elif item.__class__.__name__ == "OccupationItem":
#             self.insert_occupation(item)
#         else:
#             spider.logger.warning(f"Unknown item type: {type(item)}")

#         return item

#     # ---- insert methods ----

#     def insert_occupation(self, item):
#         # existing insert query
#         query = """
#             INSERT INTO occupations (
#                 preferred_title, alt_label, description, isco_code, uri, is_leaf, is_functional_leaf
#             )
#             VALUES %s
#             ON CONFLICT (uri) DO NOTHING;
#         """

#         is_functional_leaf = bool(item.get('essentialSkills') or item.get('optionalSkills'))
#         is_leaf = not (item.get('narrowerOccupation') or item.get('narrowerConcept'))

#         values = [(
#             item.get('preferredTitle'),
#             item.get('altLabel'),
#             item.get('description'),
#             item.get('isco_code'),
#             item.get('uri'),
#             is_leaf,
#             is_functional_leaf
#         )]

#         execute_values(self.cursor, query, values)

#     def insert_skill(self, item):
#         query = """
#             INSERT INTO skills (uri, skill_type, preferred_label, description)
#             VALUES %s
#             ON CONFLICT (uri) DO NOTHING;
#         """
#         values = [
#             (
#                 item.get("uri"),
#                 item.get("skill_type"),
#                 item.get("preferred_label"),
#                 item.get("description"),
#             )
#         ]
#         execute_values(self.cursor, query, values)

#     def insert_occupation_relations(self, item):
#         # broaderISCOGroup
#         if item.get("broaderISCOGroup"):
#             self._insert_relation(
#                 "occupation_relations",
#                 item["broaderISCOGroup"],
#                 item["uri"],
#                 "broaderISCOGroup",
#             )

#         # broaderConcept
#         if item.get("broaderConcept"):
#             self._insert_relation(
#                 "occupation_relations",
#                 item["broaderConcept"],
#                 item["uri"],
#                 "broaderConcept",
#             )

#         # narrowerOccupation
#         for child in item.get("narrowerOccupation", []):
#             self._insert_relation(
#                 "occupation_relations", item["uri"], child, "narrowerOccupation"
#             )

#     def insert_skill_relations(self, item):
#         if item.get("broaderConcept"):
#             self._insert_relation(
#                 "skill_relations", item["broaderConcept"], item["uri"], "broaderConcept"
#             )

#         for child in item.get("narrowerConcepts", []):
#             self._insert_relation(
#                 "skill_relations", item["uri"], child, "narrowerConcept"
#             )

#     def _insert_relation(self, table, parent_uri, child_uri, rel_type):
#         query = f"""
#             INSERT INTO {table} (parent_uri, child_uri, relation_type)
#             VALUES (%s, %s, %s)
#             ON CONFLICT DO NOTHING;
#         """
#         self.cursor.execute(query, (parent_uri, child_uri, rel_type))

#     def insert_relationships(self, occupation_uri, skills, rel_type):
#         query = """
#             INSERT INTO occupation_skills (occupation_uri, skill_uri, relation_type)
#             VALUES %s
#             ON CONFLICT DO NOTHING;
#         """
#         values = [
#             (occupation_uri, s.get("uri"), rel_type) for s in skills if s.get("uri")
#         ]
#         if values:
#             execute_values(self.cursor, query, values)
