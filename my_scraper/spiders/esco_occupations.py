import json

import scrapy


class EscoOccupationsSpider(scrapy.Spider):
    name = "esco_occupations"
    allowed_domains = ["ec.europa.eu"]
    start_urls = [
        "https://ec.europa.eu/esco/api/resource/concept?uri=http://data.europa.eu/esco/isco/C1&language=en",
    ]

    def parse(self, response):
        data = json.loads(response.body)
