import json
import re

import scrapy

from ..loaders import OccupationLoader, SkillLoader


class EscoOccupationsSpider(scrapy.Spider):
    name = "esco_occupations"
    allowed_domains = ["ec.europa.eu"]
    start_urls = [
        "https://ec.europa.eu/esco/api/resource/occupation?uri=http://data.europa.eu/esco/isco/C0&language=en",
    ]

    def parse(self, response):
        data = json.loads(response.body)
        item = OccupationLoader(response=response)
        item.add_value("preferred_title", data.get("title"))
        item.add_value("alt_label", data.get("preferredLabel").get("en"))
        item.add_value("description", data.get("description").get("en").get("literal"))
        item.add_value("isco_code", data.get("code"))
        item.add_value("uri", data.get("uri"))
        hierarchy = data.get("_links")
        if hierarchy.get("broaderIscoGroup"):
            item.add_value("broader_isco_group", hierarchy.get("broaderIscoGroup"))
        elif hierarchy.get("broaderConcept"):
            item.add_value("broader_concept", hierarchy.get("broaderConcept"))
        elif hierarchy.get("narrowerConcept"):
            item.add_value("narrower_concept", hierarchy.get("narrowerConcept"))
        elif hierarchy.get("narrowerOccupation"):
            item.add_value("narrower_occupation", hierarchy.get("narrowerOccupation"))

        item.add_value(
            "essential_skills", self._get_skills(response, hierarchy, "Essential")
        )
        item.add_value(
            "optional_skills", self._get_skills(response, hierarchy, "Optional")
        )

        yield item.load_item()

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
