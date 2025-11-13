import json
from urllib.parse import urlencode

import scrapy

from my_tools import extract_root_code, generate_skill_code

from ..items import SkillHierarchyItem
from ..loaders import SkillLoader


class EscoSkillsSpider(scrapy.Spider):
    name = "esco_skills"
    allowed_domains = ["ec.europa.eu"]
    start_urls = [
        # Starting from the sectoral skill root
        "https://ec.europa.eu/esco/api/resource/skill?uri=http://data.europa.eu/esco/skill/S1.1&language=en",
        # Uncomment others when you want to crawl more domains
        # "https://ec.europa.eu/esco/api/resource/skill?uri=http://data.europa.eu/esco/skill/K&language=en",
        # "https://ec.europa.eu/esco/api/resource/skill?uri=http://data.europa.eu/esco/skill/L&language=en",
        # "https://ec.europa.eu/esco/api/resource/skill?uri=http://data.europa.eu/esco/skill/T&language=en",
    ]

    visited_uris = set()
    code_lookup = {}  # store parent_uri -> generated code

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

        # Broader relationships
        broader_uris = []
        for key in ("broaderConcept", "broaderSkill", "broaderHierarchyConcept"):
            if hierarchy.get(key):
                broader_uris.extend(
                    [g.get("uri") for g in hierarchy[key] if g.get("uri")]
                )

        if broader_uris:
            item.add_value("broader_skill_uri", broader_uris)

        # Generate the new skill code
        if root_code := extract_root_code(uri):
            skill_code = root_code
        else:
            skill_code = generate_skill_code(uri, broader_uris, self.code_lookup)

        item.add_value("skill_code", skill_code)
        self.code_lookup[uri] = skill_code

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
            yield scrapy.Request(url=next_url, callback=self.parse)
