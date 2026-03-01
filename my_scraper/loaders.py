# my_scraper/loaders.py

import re

import ftfy
from itemloaders import ItemLoader
from itemloaders.processors import Identity, Join, MapCompose, TakeFirst

from .items import OccupationItem, SkillItem


def fix_encoding(s):
    """Fix mojibake and encoding errors (e.g. Â\xa0 → space, â€™ → ')."""
    if not isinstance(s, str):
        return s
    return ftfy.fix_text(s)


def remove_white_space(s):
    if not isinstance(s, str):
        return s
    return re.sub(r"\s+", " ", s)


def string_strip(s):
    if not isinstance(s, str):
        return s
    return s.strip()


def filter_empty(s):
    if isinstance(s, str):
        return s or None
    return s


class BaseESCOLoader(ItemLoader):
    """
    Base loader for ESCO-related data.
    Keeps JSON structure intact by default.
    """

    default_input_processor = MapCompose(
        fix_encoding, remove_white_space, string_strip, filter_empty
    )
    default_output_processor = TakeFirst()


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
    skill_code_out = TakeFirst()
    uri_out = TakeFirst()
    class_name_out = TakeFirst()
    status_out = TakeFirst()
    is_leaf_out = TakeFirst()
    is_functional_leaf_out = TakeFirst()

    # Preserve structured data fields
    essential_skills_out = Identity()
    optional_skills_out = Identity()
    narrower_concept_out = Identity()
    narrower_occupation_out = Identity()
    broader_isco_group_uri_out = Identity()


class SkillLoader(BaseESCOLoader):
    """
    Loader for SkillItem.
    Most fields are structured, so Identity() is used for both input and output.
    """

    default_item_class = SkillItem

    # Scalar fields (flatten lists to single values)
    preferred_title_out = TakeFirst()
    alt_label_out = TakeFirst()
    description_out = TakeFirst()
    skill_type_out = TakeFirst()
    isco_code = TakeFirst()
    class_name_out = TakeFirst()
    uri_out = TakeFirst()
    scope_note_out = TakeFirst()
    is_leaf_out = TakeFirst()
    is_functional_leaf_out = TakeFirst()

    # Preserve structured data fields
    reuse_level_out = TakeFirst()
    broader_skill_uri_out = Join(", ")
