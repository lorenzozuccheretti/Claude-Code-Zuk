"""Shared fixtures. Every test writes into its own tmp_path — no run pollutes another."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from kdp_factory.config import EngineConfig
from kdp_factory.niche import niche_from_dict

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"

GOOD_NICHE: dict = {
    "niche": "Gratitude journal for new mothers",
    "book_type": "journal",
    "audience": "first-time mothers in the first year, usually buying for themselves",
    "promise": "Five quiet minutes at the end of a day that had none.",
    "keywords_seed": [
        "gratitude journal for moms",
        "new mom gift journal",
        "postpartum journal for mothers",
        "motherhood keepsake journal",
    ],
    "signals": {
        "demand_volume": {"score": 7.5, "evidence": "1,900 searches/mo", "source": "Helium 10"},
        "proven_sales": {"score": 7.0, "evidence": "top 3 at BSR 14k-58k", "source": "Amazon"},
        "competition_gap": {"score": 8.0, "evidence": "reviews cite bleed-through"},
        "differentiation": {"score": 7.5, "evidence": "no first-year-specific incumbent"},
        "review_moat": {"score": 4.0, "evidence": "412/188/96 reviews"},
        "price_headroom": {"score": 6.5, "evidence": "incumbents at 8.99-12.99"},
        "format_fit": {"score": 9.0, "evidence": "one page a day suits five spare minutes"},
        "longevity": {"score": 8.0, "evidence": "cohort renews yearly"},
        "seasonality": {"score": 3.0, "evidence": "baseline holds all year"},
        "trademark_risk": {"score": 1.0, "evidence": "no live marks"},
    },
    "competitor": {
        "title": "The Gratitude Journal for Moms",
        "price": 9.99,
        "reviews": 412,
        "gaps": ["pages bleed through", "prompts repeat every week"],
    },
    "trademark": {"checked": True, "source": "USPTO TESS", "checked_on": "2026-01-15", "hits": []},
    "constraints": {"trim_size": "6x9", "paper": "bw_cream", "target_pages": 120},
}


@pytest.fixture
def good_niche():
    return niche_from_dict(copy.deepcopy(GOOD_NICHE))


@pytest.fixture
def niche_dict():
    return copy.deepcopy(GOOD_NICHE)


@pytest.fixture
def config(tmp_path: Path) -> EngineConfig:
    return EngineConfig(
        output_root=tmp_path / "output",
        telemetry_path=tmp_path / "output" / "_telemetry" / "gates.jsonl",
    )
