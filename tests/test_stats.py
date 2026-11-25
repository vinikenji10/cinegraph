"""Testes estatísticos da avaliação. Valores de referência conferidos com scipy.stats."""

import math

import numpy as np
import pytest

from cinegraph.evaluation import stats


def test_per_class_metrics_one_vs_rest():
    confusion = {
        "sql": {"sql": 30, "vector": 0, "hybrid": 0, "outro": 0},
        "vector": {"sql": 0, "vector": 28, "hybrid": 2, "outro": 0},
        "hybrid": {"sql": 0, "vector": 0, "hybrid": 30, "outro": 0},
        "outro": {"sql": 0, "vector": 0, "hybrid": 0, "outro": 5},
    }
    m = stats.per_class_metrics(confusion)
    assert m["vector"]["recall"] == pytest.approx(28 / 30)
    assert m["hybrid"]["precision"] == pytest.approx(30 / 32)
    assert m["hybrid"]["specificity"] == pytest.approx(63 / 65)
    assert m["macro"]["precision"] == pytest.approx((1 + 1 + 30 / 32 + 1) / 4)


def test_mcnemar_exact():
    a = np.array([1] * 25 + [1] * 5)
    b = np.array([0] * 25 + [1] * 5)
    t = stats.mcnemar_exact(a, b)
    assert (t["a_only"], t["b_only"]) == (25, 0)
    assert t["p"] == pytest.approx(2 / 2**25)  # scipy.stats.binomtest(0, 25).pvalue
    assert stats.mcnemar_exact(a, a)["p"] == 1.0


def test_wilcoxon_matches_scipy_with_ties():
    a = np.array([1.0, 0.8, 1.0, 0.6, 1.0, 0.8, 1.0, 1.0, 0.4, 1.0])
    b = np.array([0.8, 0.8, 0.6, 0.6, 1.0, 0.4, 0.8, 0.6, 0.6, 0.2])
    # scipy.stats.wilcoxon(a, b, method="approx").pvalue (aproximação normal com correção de empates)
    assert stats.wilcoxon_signed_rank(a, b)["p"] == pytest.approx(0.039592, rel=1e-3)
    assert stats.wilcoxon_signed_rank(a, a)["p"] == 1.0


def test_fisher_exact():
    # scipy.stats.fisher_exact([[1, 9], [11, 3]]).pvalue
    assert stats.fisher_exact(1, 10, 11, 14)["p"] == pytest.approx(0.002759, rel=1e-3)
    assert stats.fisher_exact(0, 373, 87, 508)["p"] < 1e-20


def test_holm():
    adj = stats.holm({"a": 0.01, "b": 0.04, "c": 0.03})
    assert adj == {"a": pytest.approx(0.03), "c": pytest.approx(0.06), "b": pytest.approx(0.06)}
    assert math.isclose(stats.holm({"x": 0.058})["x"], 0.058)
