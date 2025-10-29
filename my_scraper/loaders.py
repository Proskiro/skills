from itemloaders.processors import Identity, TakeFirst
from scrapy.loader import ItemLoader


class OccupationLoader(ItemLoader):
    default_output_processor = TakeFirst()
    alt_labels_out = Identity()
    essential_skill_uris_out = Identity()
    optional_skill_uris_out = Identity()
