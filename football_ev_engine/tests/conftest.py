from __future__ import annotations

import pandas as pd
import pytest

from src.data.database import connect, init_schema, upsert_df
from tests.synthetic import generate_matches

SEASONS = ["2022-2023", "2023-2024", "2024-2025"]


@pytest.fixture(scope="session")
def synthetic_matches() -> pd.DataFrame:
    return generate_matches(SEASONS)


@pytest.fixture
def con():
    c = connect(":memory:")
    init_schema(c)
    yield c
    c.close()


@pytest.fixture
def loaded_con(con, synthetic_matches):
    upsert_df(con, "matches", synthetic_matches)
    return con
