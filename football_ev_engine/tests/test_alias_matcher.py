import json

from src.data.alias_matcher import AliasMatcher, export_aliases, import_aliases, normalise, set_manual_alias

CANON = ["Inter", "Milan", "Roma", "Lazio", "Verona", "Atalanta", "Man United", "Man City"]


def matcher(con, **kw):
    return AliasMatcher(con, "I1", CANON, **kw)


def test_normalise():
    assert normalise("AC Milan") == "milan"
    assert normalise("1. FC Köln") == "koln"
    assert normalise("Atalanta BC") == "atalanta"


def test_resolution_methods(con):
    m = matcher(con, seed={"Inter Milan": "Inter", "Manchester United": "Man United"})
    assert m.match("Roma").method == "exact"
    assert (r := m.match("AS Roma")).canonical_name == "Roma" and r.method == "exact"  # after normalisation
    assert m.match("Inter Milan").canonical_name == "Inter"
    assert m.match("Inter Milan").method == "cached"
    assert m.match("Manchester United").canonical_name == "Man United"
    fz = m.match("Hellas Veronaa")
    assert fz.canonical_name == "Verona" and fz.method == "fuzzy" and fz.score >= 80


def test_below_threshold_is_unmatched_and_not_cached(con):
    m = matcher(con, seed={})
    r = m.match("Barcelona")
    assert r.canonical_name is None and r.method == "unmatched"
    assert con.execute("SELECT count(*) FROM team_aliases").fetchone()[0] == 0


def test_batch_is_one_to_one(con):
    m = matcher(con, seed={})
    # "Milan" matches exactly, so a fuzzy "AC Milano" must not also claim it.
    out = m.match_many(["AC Milano", "Milan"])
    assert out["Milan"].canonical_name == "Milan"
    assert out["AC Milano"].canonical_name != "Milan"


def test_manual_alias_wins_and_is_verified(con):
    set_manual_alias(con, "I1", "Internazionale", "Inter")
    r = matcher(con, seed={}).match("Internazionale")
    assert r.canonical_name == "Inter" and r.method == "manual"
    assert con.execute("SELECT verified FROM team_aliases WHERE raw_name = 'Internazionale'").fetchone()[0]


def test_fuzzy_is_stored_unverified(con):
    matcher(con, seed={}).match("Atalantaa")
    assert con.execute("SELECT verified FROM team_aliases WHERE raw_name = 'Atalantaa'").fetchone()[0] is False


def test_export_import_roundtrip(con, tmp_path):
    set_manual_alias(con, "I1", "Internazionale", "Inter")
    path = tmp_path / "aliases.json"
    assert export_aliases(con, path) == 1
    assert json.loads(path.read_text())[0]["canonical_name"] == "Inter"
    con.execute("DELETE FROM team_aliases")
    assert import_aliases(con, path) == 1
    assert con.execute("SELECT method FROM team_aliases").fetchone()[0] == "manual"


def test_shipped_seed_file_loads():
    from src.data.alias_matcher import load_seed

    seed = load_seed()
    assert seed["Inter Milan"] == "Inter" and "_comment" not in seed
