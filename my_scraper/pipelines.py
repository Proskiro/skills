import psycopg2


class PostgresPipeline:
    def open_spider(self, spider):
        self.connection = psycopg2.connect(
            host="skillsdb.cxq4ookmeq59.eu-west-2.rds.amazonaws.com",
            database="skillsdb",
            user="adminskillsdb",
            password="Profession-skills-c0urse",
        )
        self.cursor = self.connection.cursor()

    def process_item(self, item, spider):
        self.cursor.execute(
            "INSERT INTO skills (id, name, description) VALUES (%s, %s, %s)",
            (item["id"], item["name"], item["description"]),
        )
        self.connection.commit()
        return item

    def close_spider(self, spider):
        self.cursor.close()
        self.connection.close()
