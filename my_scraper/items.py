# Define here the models for your scraped items
#
# See documentation in:
# https://docs.scrapy.org/en/latest/topics/items.html

import scrapy
from scrapy.loader import ItemLoader


class OccupationItem(scrapy.Item):
    preferred_label = scrapy.Field()
    alt_label = scrapy.Field()
    description = scrapy.Field()
    isco_code = scrapy.Field()
    uri = scrapy.Field()
    concept_type = scrapy.Field()
    broader = scrapy.Field()
    narrower = scrapy.Field()
    essential_skills = scrapy.Field()
    optional_skills = scrapy.Field()
    is_leaf_node = scrapy.Field()


class SkillItem(scrapy.Item):
    uri = scrapy.Field()
    skillType = scrapy.Field()
    preferredLabel = scrapy.Field()
    description = scrapy.Field()


class SkillLoader(ItemLoader):
    default_item_class = SkillItem
