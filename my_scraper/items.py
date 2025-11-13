# Define here the models for your scraped items
#
# See documentation in:
# https://docs.scrapy.org/en/latest/topics/items.html

import scrapy


class OccupationItem(scrapy.Item):
    preferred_title = scrapy.Field()
    alt_label = scrapy.Field()
    description = scrapy.Field()
    isco_code = scrapy.Field()
    uri = scrapy.Field()
    class_name = scrapy.Field()  # used for link to api
    broader_isco_group_uri = scrapy.Field()
    narrower_concept = scrapy.Field()
    narrower_occupation = scrapy.Field()
    essential_skills = scrapy.Field()  # array of skills items
    optional_skills = scrapy.Field()  # array of skills items
    is_leaf_node = scrapy.Field()


class OccupationHierarchyItem(scrapy.Item):
    parent_uri = scrapy.Field()
    child_uri = scrapy.Field()
    relation_type = scrapy.Field()


class SkillItem(scrapy.Item):
    preferred_title = scrapy.Field()
    alt_label = scrapy.Field()
    description = scrapy.Field()
    skill_code = scrapy.Field()
    uri = scrapy.Field()
    skill_type = scrapy.Field()
    scope_note = scrapy.Field()
    reuse_level = scrapy.Field()
    class_name = scrapy.Field()
    broader_skill_uri = scrapy.Field()
    narrower_concept = scrapy.Field()
    narrower_skill = scrapy.Field()
    is_leaf_node = scrapy.Field()


class SkillHierarchyItem(scrapy.Item):
    parent_uri = scrapy.Field()
    child_uri = scrapy.Field()
    relation_type = scrapy.Field()
