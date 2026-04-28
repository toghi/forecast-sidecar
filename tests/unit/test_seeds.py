"""T029 — set_seed reproducibility."""

from __future__ import annotations

import random

import numpy as np

from forecast_sidecar.seeds import set_seed


def test_set_seed_makes_numpy_deterministic() -> None:
    set_seed(42)
    a = np.random.rand(10)
    set_seed(42)
    b = np.random.rand(10)
    assert (a == b).all()


def test_set_seed_makes_python_random_deterministic() -> None:
    set_seed(7)
    a = [random.random() for _ in range(5)]
    set_seed(7)
    b = [random.random() for _ in range(5)]
    assert a == b


def test_different_seeds_diverge() -> None:
    set_seed(1)
    a = np.random.rand(10)
    set_seed(2)
    b = np.random.rand(10)
    assert not (a == b).all()
