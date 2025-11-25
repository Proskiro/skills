import json
import re
from urllib.parse import urlencode

import scrapy

from ...items import OccupationHierarchyItem
from ...loaders import OccupationLoader, SkillLoader


class EscoOccupationsSpider(scrapy.Spider):
    name = "esco_occupations"
    allowed_domains = ["ec.europa.eu"]
    start_urls = [
        "https://ec.europa.eu/esco/api/resource/occupation?uri=http://data.europa.eu/esco/isco/C0&language=en",
        "https://ec.europa.eu/esco/api/resource/occupation?uri=http://data.europa.eu/esco/isco/C1&language=en",
        "https://ec.europa.eu/esco/api/resource/occupation?uri=http://data.europa.eu/esco/isco/C2&language=en",
        "https://ec.europa.eu/esco/api/resource/occupation?uri=http://data.europa.eu/esco/isco/C3&language=en",
        "https://ec.europa.eu/esco/api/resource/occupation?uri=http://data.europa.eu/esco/isco/C4&language=en",
        "https://ec.europa.eu/esco/api/resource/occupation?uri=http://data.europa.eu/esco/isco/C5&language=en",
        "https://ec.europa.eu/esco/api/resource/occupation?uri=http://data.europa.eu/esco/isco/C6&language=en",
        "https://ec.europa.eu/esco/api/resource/occupation?uri=http://data.europa.eu/esco/isco/C7&language=en",
        "https://ec.europa.eu/esco/api/resource/occupation?uri=http://data.europa.eu/esco/isco/C8&language=en",
        "https://ec.europa.eu/esco/api/resource/occupation?uri=http://data.europa.eu/esco/isco/C9&language=en",
    ]
    visited_uris = set()

    def parse(self, response):
        data = json.loads(response.body)
        item = OccupationLoader(response=response)
        item.add_value("preferred_title", data.get("title"))
        item.add_value("alt_label", data.get("preferredLabel").get("en"))
        item.add_value("description", data.get("description").get("en").get("literal"))
        item.add_value("isco_code", f"C{data.get('code')}")
        item.add_value("uri", data.get("uri"))
        item.add_value("class_name", data.get("className"))
        if data.get("uri") in self.visited_uris:
            return
        self.visited_uris.add(data.get("uri"))
        hierarchy = data.get("_links")

        if hierarchy.get("broaderIscoGroup"):
            uris = [g.get("uri") for g in hierarchy["broaderIscoGroup"] if g.get("uri")]
            item.add_value("broader_isco_group_uri", ", ".join(uris))

        item.add_value(
            "essential_skills", self._get_skills(response, hierarchy, "Essential")
        )
        item.add_value(
            "optional_skills", self._get_skills(response, hierarchy, "Optional")
        )

        has_narrower_occupation = bool(hierarchy.get("narrowerOccupation"))
        has_skills = bool(
            hierarchy.get("hasEssentialSkill") or hierarchy.get("hasOptionalSkill")
        )

        is_leaf = not has_narrower_occupation and has_skills
        is_functional_leaf = has_narrower_occupation and has_skills  # ✅ Functional if it actually carries skills

        item.add_value("is_leaf", is_leaf)
        item.add_value("is_functional_leaf", is_functional_leaf)

        yield item.load_item()

        narrower_links = []
        if hierarchy.get("narrowerConcept"):
            narrower_links.extend(hierarchy.get("narrowerConcept"))
        if hierarchy.get("narrowerOccupation"):
            narrower_links.extend(hierarchy.get("narrowerOccupation"))

        # For all narrower (children)
        for link in narrower_links:
            child_uri = link.get("uri")
            if not child_uri:
                continue

            yield OccupationHierarchyItem(
                parent_uri=item.get_output_value("uri"),
                child_uri=child_uri,
                relation_type="narrower",
            )

            next_url = "https://ec.europa.eu/esco/api/resource/occupation?" + urlencode(
                {"uri": child_uri, "language": "en"}
            )
            yield scrapy.Request(url=next_url, callback=self.parse)

    def _get_skills(self, response, hierarchy, flag):
        skills = []
        if flag and hierarchy.get(f"has{flag}Skill"):
            for skill in hierarchy.get(f"has{flag}Skill"):
                s_loader = SkillLoader(response=response)
                s_loader.add_value("uri", skill.get("uri"))
                skillType = re.search(r"([^/]+)$", skill.get("skillType")).group(1)
                s_loader.add_value("skill_type", skillType)
                s_loader.add_value("preferred_title", skill.get("title"))
                skills.append(s_loader.load_item())
        return skills
