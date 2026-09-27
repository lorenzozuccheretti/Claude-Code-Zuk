from src.data.database import load_matches, table_columns, upsert_df


def test_schema_tables(con):
    tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
    assert {"matches", "fixtures", "odds", "team_aliases", "model_params", "api_usage", "value_bets"} <= tables
    assert "b365h" in table_columns(con, "matches")


def test_upsert_is_idempotent(con, synthetic_matches):
    upsert_df(con, "matches", synthetic_matches)
    upsert_df(con, "matches", synthetic_matches)
    assert con.execute("SELECT count(*) FROM matches").fetchone()[0] == len(synthetic_matches)


def test_upsert_replaces_on_key(con, synthetic_matches):
    first = synthetic_matches.head(1).copy()
    upsert_df(con, "matches", first)
    first["fthg"] = 7
    upsert_df(con, "matches", first)
    assert con.execute("SELECT fthg FROM matches").fetchone()[0] == 7


def test_load_matches_filters(loaded_con):
    df = load_matches(loaded_con, "I1", ["2023-2024"])
    assert set(df["season"]) == {"2023-2024"} and df["match_date"].is_monotonic_increasing
    assert load_matches(loaded_con, "E0").empty
