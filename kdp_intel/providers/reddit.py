"""Reddit threads from Italian subreddits, for the reader's own words.

With ``REDDIT_CLIENT_ID``/``REDDIT_CLIENT_SECRET`` the app-only OAuth flow is
used (oauth.reddit.com, higher limits). Without them the public JSON
endpoints are tried, which Reddit may rate-limit or refuse.
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx

from .base import ProviderError, check, http_client


class Reddit:
    def __init__(self, client_id: str = "", client_secret: str = "",
                 client: httpx.Client | None = None) -> None:
        self.client = client or http_client()
        self.base = "https://www.reddit.com"
        if client_id and client_secret:
            token = check(self.client.post(
                "https://www.reddit.com/api/v1/access_token",
                auth=(client_id, client_secret), data={"grant_type": "client_credentials"},
            ), "reddit")
            assert isinstance(token, dict)
            self.client.headers["Authorization"] = f"Bearer {token['access_token']}"
            self.base = "https://oauth.reddit.com"

    def _get(self, path: str, params: dict) -> dict:
        data = check(self.client.get(f"{self.base}{path}", params={**params, "raw_json": 1}), "reddit")
        if not isinstance(data, (dict, list)):
            raise ProviderError("reddit: unexpected payload")
        return data  # type: ignore[return-value]

    def threads(self, subreddit: str, query: str, limit: int = 25, comments: int = 20) -> list[dict]:
        """Matching posts from the last year, each with its top comments."""
        listing = self._get(f"/r/{subreddit}/search.json", {
            "q": query, "restrict_sr": 1, "sort": "relevance", "t": "year", "limit": limit,
        })
        out = []
        for child in listing.get("data", {}).get("children", []):
            post = child.get("data", {})
            thread = self._get(f"/r/{subreddit}/comments/{post.get('id')}.json",
                               {"limit": comments, "sort": "top"})
            replies = []
            if isinstance(thread, list) and len(thread) > 1:
                replies = [c["data"].get("body", "") for c in thread[1]["data"]["children"]
                           if c.get("kind") == "t1"]
            out.append({
                "url": f"https://www.reddit.com{post.get('permalink', '')}",
                "title": post.get("title", ""),
                "text": post.get("selftext", ""),
                "comments": [r for r in replies if r],
                "created": datetime.fromtimestamp(post.get("created_utc", 0), timezone.utc).date().isoformat(),
                "score": post.get("score", 0),
            })
        return out
