import numpy as np
import pandas as pd
import pytest

from src.models.ml_classifier import MLBenchmark, build_features


def test_features_use_only_past_matches(synthetic_matches):
    df = synthetic_matches.head(300).reset_index(drop=True)
    base = build_features(df)
    altered = df.copy()
    altered.loc[200:, ["fthg", "ftag"]] = 9  # rewrite the future
    after = build_features(altered)
    pd.testing.assert_frame_equal(base.loc[:200], after.loc[:200])  # row 200's own features unchanged too
    assert not base.loc[250:].equals(after.loc[250:])


def test_first_matches_have_no_features(synthetic_matches):
    feats = build_features(synthetic_matches.head(30))
    assert feats.iloc[:10].isna().all(axis=1).all()


@pytest.mark.parametrize("kind", ["logreg", "gbm"])
def test_fit_predict(synthetic_matches, kind, tmp_path):
    clf = MLBenchmark(kind=kind).fit(synthetic_matches.iloc[:600])
    proba = clf.predict_proba_history(synthetic_matches)
    ok = proba.dropna()
    assert len(ok) > 500
    np.testing.assert_allclose(ok.sum(axis=1), 1.0)
    path = tmp_path / "m.joblib"
    clf.save(path)
    again = MLBenchmark.load(path).predict_proba_history(synthetic_matches)
    pd.testing.assert_frame_equal(proba, again)


def test_unknown_kind(synthetic_matches):
    with pytest.raises(ValueError):
        MLBenchmark(kind="xgb").fit(synthetic_matches)
