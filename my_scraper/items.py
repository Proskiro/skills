# Define here the models for your scraped items
#
# See documentation in:
# https://docs.scrapy.org/en/latest/topics/items.html

import scrapy


class OccupationItem(scrapy.Item):
    uri = scrapy.Field()
    preferred_label = scrapy.Field()
    alt_labels = scrapy.Field()
    description = scrapy.Field()
    isco_group = scrapy.Field()
    concept_type = scrapy.Field()
    status = scrapy.Field()
    modified_date = scrapy.Field()
    broader_uris = scrapy.Field()
    narrower_uris = scrapy.Field()
    essential_skill_uris = scrapy.Field()
    optional_skill_uris = scrapy.Field()
    source_language = scrapy.Field()
    version = scrapy.Field()
    source_url = scrapy.Field()
    scraped_at = scrapy.Field()


class SkillItem(scrapy.Item):
    uri = scrapy.Field()
    preferred_label = scrapy.Field()
    alt_labels = scrapy.Field()
    description = scrapy.Field()
    concept_type = scrapy.Field()
    reuse_level = scrapy.Field()
    skill_type = scrapy.Field()
    status = scrapy.Field()
    broader_uris = scrapy.Field()
    narrower_uris = scrapy.Field()
    modified_date = scrapy.Field()
    source_url = scrapy.Field()


class OccupationSkillItem(scrapy.Item):
    occupation_uri = scrapy.Field()
    skill_uri = scrapy.Field()
    skill_type = scrapy.Field()  # "essential" or "optional"
