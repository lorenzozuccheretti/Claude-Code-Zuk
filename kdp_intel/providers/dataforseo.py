"""DataForSEO: Amazon keyword volumes and Google keyword volumes for Italy.

* Amazon: ``/v3/dataforseo_labs/amazon/bulk_search_volume/live``
* Google: ``/v3/keywords_data/google_ads/search_volume/live``

Both take ``location_code`` 2380 (Italy) and ``language_code`` "it". If the
Amazon endpoint does not cover a location, DataForSEO says so in the task's
``status_message``; we raise that message rather than return zeros, because
"no data" and "zero searches" lead to opposite decisions.
"""

from __future__ import annotations

import httpx

from ..models import KeywordMetric
from .base import ProviderError, check, http_client

BASE = "https://api.dataforseo.com/v3"
ITALY = 2380


class DataForSEO:
    def __init__(
        self, login: str, password: str, location_code: int = ITALY, language_code: str = "it",
        client: httpx.Client | None = None,
    ) -> None:
        if not (login and password):
            raise ProviderError("DATAFORSEO_LOGIN and DATAFORSEO_PASSWORD must both be set")
        self.auth = (login, password)
        self.location_code, self.language_code = location_code, language_code
        self.client = client or http_client(timeout=120.0)

    def _post(self, path: str, payload: list[dict]) -> list[dict]:
        data = check(self.client.post(f"{BASE}/{path}", auth=self.auth, json=payload), "dataforseo")
        assert isinstance(data, dict)
        if data.get("status_code") != 20000:
            raise ProviderError(f"dataforseo: {data.get('status_message')}")
        items: list[dict] = []
        for task in data.get("tasks", []):
            if task.get("status_code") != 20000:
                raise ProviderError(f"dataforseo task: {task.get('status_message')}")
            for result in task.get("result") or []:
                # Labs endpoints nest rows under "items"; Keywords Data returns rows directly.
                items.extend(result.get("items") or ([result] if "keyword" in result else []))
        return items

    def amazon_volumes(self, keywords: list[str]) -> list[KeywordMetric]:
        rows = self._post("dataforseo_labs/amazon/bulk_search_volume/live", [{
            "keywords": keywords, "location_code": self.location_code,
            "language_code": self.language_code,
        }])
        return [KeywordMetric(keyword=r["keyword"], search_volume=r.get("search_volume"),
                              source="dataforseo:amazon") for r in rows]

    def google_volumes(self, keywords: list[str]) -> list[KeywordMetric]:
        rows = self._post("keywords_data/google_ads/search_volume/live", [{
            "keywords": keywords, "location_code": self.location_code,
            "language_code": self.language_code,
        }])
        return [KeywordMetric(keyword=r["keyword"], search_volume=r.get("search_volume"),
                              source="dataforseo:google") for r in rows]

    # -- KeywordVolumes
    def volumes(self, keywords: list[str]) -> list[KeywordMetric]:
        return self.amazon_volumes(keywords)
