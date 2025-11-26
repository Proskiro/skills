import json
import re
from urllib.parse import urlencode

import scrapy

from my_tools import extract_root_code, generate_skill_code

from ...items import SkillHierarchyItem
from ...loaders import SkillLoader


class EscoSkillsSpider(scrapy.Spider):
    name = "esco_skills"
    allowed_domains = ["ec.europa.eu"]
    start_urls = [
        # Starting from the sectoral skill root
        # "https://ec.europa.eu/esco/api/resource/skill?uri=http://data.europa.eu/esco/skill/S&language=en",
        # "https://ec.europa.eu/esco/api/resource/skill?uri=http://data.europa.eu/esco/skill/K&language=en",
        "https://ec.europa.eu/esco/api/resource/skill?uri=http://data.europa.eu/esco/skill/L&language=en",
    ]

    visited_uris = set()
    code_lookup = {}  # store parent_uri -> generated code

    def start_requests(self):
        for url in self.start_urls:
            yield scrapy.Request(url, meta={"root_url": url}, callback=self.parse)

    def parse(self, response):
        data = json.loads(response.body)
        uri = data.get("uri")

        if uri in self.visited_uris:
            return
        self.visited_uris.add(uri)

        item = SkillLoader(response=response)
        item.add_value("preferred_title", data.get("title"))

        # Optional/alt labels
        if data.get("alternativeLabel"):
            item.add_value("alt_label", data.get("alternativeLabel").get("en"))

        # Description
        if data.get("description") and data["description"].get("en"):
            item.add_value("description", data["description"]["en"].get("literal"))

        # Scope note (optional)
        if data.get("scope_note") and data["scope_note"].get("en"):
            item.add_value("scope_note", data["scope_note"]["en"].get("literal"))

        # URI + class
        item.add_value("uri", uri)
        item.add_value("class_name", data.get("className"))

        hierarchy = data.get("_links") or {}

        # Reuse level
        if hierarchy.get("hasReuseLevel"):
            item.add_value("reuse_level", hierarchy["hasReuseLevel"][0].get("code"))

        # Skill type
        item.add_value("skill_type", self._get_skill_type(response, item, hierarchy))

        # Broader relationships
        broader_uris = []
        for key in ("broaderConcept", "broaderSkill", "broaderHierarchyConcept"):
            if hierarchy.get(key):
                broader_uris.extend(
                    [g.get("uri") for g in hierarchy[key] if g.get("uri")]
                )

        if broader_uris:
            # Reorder: coded parents first, UUID parents last
            coded = [u for u in broader_uris if extract_root_code(u)]
            uuid = [u for u in broader_uris if not extract_root_code(u)]
            ordered_broader_uris = coded + uuid

            item.add_value("broader_skill_uri", ordered_broader_uris)
        else:
            ordered_broader_uris = []

        # Generate the new skill code
        if root_code := extract_root_code(uri):
            skill_code = root_code
        else:
            skill_code = generate_skill_code(
                uri, ordered_broader_uris, self.code_lookup
            )

        item.add_value("skill_code", skill_code)
        self.code_lookup[uri] = skill_code

        has_narrower_skill = bool(hierarchy.get("narrowerSkill"))
        has_narrower_concept = bool(hierarchy.get("narrowerConcept"))

        # Structural leaf: bottom-most skill (no narrower skill/concept)
        is_leaf = not has_narrower_skill and not has_narrower_concept

        # Functional leaf: skill that has narrower sub-skills (so it’s a functional category)
        is_functional_leaf = has_narrower_skill

        item.add_value("is_leaf", is_leaf)
        item.add_value("is_functional_leaf", is_functional_leaf)

        # Yield the skill
        yield item.load_item()

        # Crawl narrower skills
        narrower_links = []
        for key in ("narrowerConcept", "narrowerSkill"):
            if hierarchy.get(key):
                narrower_links.extend(hierarchy[key])

        for link in narrower_links:
            child_uri = link.get("uri")
            if not child_uri:
                continue

            yield SkillHierarchyItem(
                parent_uri=uri,
                child_uri=child_uri,
                relation_type="narrower",
            )

            next_url = "https://ec.europa.eu/esco/api/resource/skill?" + urlencode(
                {"uri": child_uri, "language": "en"}
            )
            yield scrapy.Request(
                url=next_url,
                meta={"root_url": response.meta.get("root_url")},
                callback=self.parse,
            )

    def _get_skill_type(self, response, item, hierarchy):
        skill_type = None
        desc = item.get_output_value("description")
        if not isinstance(desc, str):
            desc = ""
        else:
            desc = (
                desc.replace("\u00a0", " ")  # convert non-breaking spaces
                .replace(" ", " ")  # sometimes NBSP is this literal
                .strip()
                .rstrip(".")  # remove trailing periods
            )

        uri = response.meta.get("root_url")
        is_language_tree = uri.endswith("skill/L&language=en")

        # 1. SPECIAL RULE FOR LANGUAGE ROOT DESCRIPTION
        # "The {language} language" → skill_type = "language"
        if is_language_tree and re.fullmatch(r"The [A-Za-z-]+ language", desc):
            return "language"

        # 2. ANY OTHER SKILL UNDER L-TREE → languageSkill
        elif is_language_tree:
            return "language skill"

        # 3. NON-L nodes: first use ESCO official type
        elif hierarchy.get("hasSkillType"):
            return hierarchy["hasSkillType"][0].get("title")

        # 4. FALLBACK: infer from root of the URI
        if skill_type is None:
            if uri.endswith("http://data.europa.eu/esco/skill/S&language=en"):
                return "skill"
            elif uri.endswith("http://data.europa.eu/esco/skill/K&language=en"):
                return "knowledge"
            else:
                return "transversal skill"

        return skill_type
