# my_scraper/loaders.py

from itemloaders import ItemLoader
from itemloaders.processors import Identity, TakeFirst

from .items import OccupationItem, SkillItem


class BaseESCOLoader(ItemLoader):
    """
    Base loader for ESCO-related data.
    Keeps JSON structure intact by default.
    """

    default_input_processor = Identity()
    default_output_processor = Identity()


class OccupationLoader(BaseESCOLoader):
    """
    Loader for OccupationItem.
    Preserves list fields for skills and hierarchical relationships.
    """

    default_item_class = OccupationItem

    # Flatten only scalar fields
    preferred_title_out = TakeFirst()
    alt_label_out = TakeFirst()
    description_out = TakeFirst()
    isco_code_out = TakeFirst()
    uri_out = TakeFirst()
    class_name_out = TakeFirst()

    # Preserve structured data fields
    essential_skills_out = Identity()
    optional_skills_out = Identity()
    narrower_concept_out = Identity()
    narrower_occupation_out = Identity()
    broader_isco_group_out = Identity()


class SkillLoader(BaseESCOLoader):
    """
    Loader for SkillItem.
    Most fields are structured, so Identity() is used for both input and output.
    """

    default_item_class = SkillItem

    # Scalar fields (flatten lists to single values)
    preferred_title_out = TakeFirst()
    description_out = TakeFirst()
    skill_type_out = TakeFirst()
    uri_out = TakeFirst()
