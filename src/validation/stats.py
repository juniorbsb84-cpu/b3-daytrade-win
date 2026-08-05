"""Probabilistic e Deflated Sharpe Ratio (Bailey & Lopez de Prado).

Por que isto existe: rodar centenas de configuracoes e ficar com a melhor
GARANTE um Sharpe alto mesmo em ruido puro. O DSR desconta exatamente esse
efeito -- ele pergunta se o Sharpe observado sobrevive ao numero de tentativas
que foram feitas para encontra-lo.

Corte adotado no projeto: DSR >= 0,95 para uma estrategia ser considerada
aprovada para execucao. Abaixo disso e "sem evidencia ainda", nao veredito
definitivo.
"""
from __future__ import annotations

import numpy as np
from scipy.stats import norm

EULER = 0.5772156649015329


def sharpe_per_obs(returns: np.ndarray) -> float:
    r = np.asarray(returns, dtype="float64")
    r = r[np.isfinite(r)]
    if len(r) < 2:
        return 0.0
    sd = r.std(ddof=1)
    return float(r.mean() / sd) if sd > 0 else 0.0


def psr(returns: np.ndarray, sr_benchmark: float = 0.0) -> float:
    """Probabilistic Sharpe Ratio. `sr_benchmark` na MESMA frequencia de `returns`."""
    r = np.asarray(returns, dtype="float64")
    r = r[np.isfinite(r)]
    T = len(r)
    if T < 3:
        return 0.0
    sd = r.std(ddof=1)
    if sd <= 0:
        return 0.0
    sr = r.mean() / sd
    z = (r - r.mean()) / sd
    g3 = float((z ** 3).mean())
    g4 = float((z ** 4).mean())
    denom = 1.0 - g3 * sr + (g4 - 1.0) / 4.0 * sr ** 2
    if denom <= 0:
        return 0.0
    stat = (sr - sr_benchmark) * np.sqrt(T - 1) / np.sqrt(denom)
    return float(norm.cdf(stat))


def expected_max_sharpe(n_trials: int, var_trials: float) -> float:
    """E[max SR] sob a hipotese nula de que todas as tentativas tem SR=0."""
    if n_trials <= 1 or var_trials <= 0:
        return 0.0
    N = float(n_trials)
    a = norm.ppf(1.0 - 1.0 / N)
    b = norm.ppf(1.0 - 1.0 / (N * np.e))
    return float(np.sqrt(var_trials) * ((1.0 - EULER) * a + EULER * b))


def deflated_sharpe(returns: np.ndarray, n_trials: int,
                    trial_sharpes: np.ndarray | None = None,
                    var_trials: float | None = None) -> dict:
    """DSR = PSR(SR0), com SR0 = E[max SR] das tentativas.

    `returns` e `trial_sharpes` devem estar na MESMA frequencia (diaria).
    """
    r = np.asarray(returns, dtype="float64")
    r = r[np.isfinite(r)]
    if var_trials is None:
        if trial_sharpes is None or len(np.asarray(trial_sharpes)) < 2:
            var_trials = 0.0
        else:
            var_trials = float(np.var(np.asarray(trial_sharpes, dtype="float64"), ddof=1))
    sr0 = expected_max_sharpe(n_trials, var_trials)
    return {
        "sharpe_per_obs": sharpe_per_obs(r),
        "sharpe_annual": sharpe_per_obs(r) * np.sqrt(252),
        "n_obs": int(len(r)),
        "n_trials": int(n_trials),
        "var_trials": float(var_trials),
        "sr0_threshold": float(sr0),
        "sr0_annual": float(sr0 * np.sqrt(252)),
        "psr_vs_zero": psr(r, 0.0),
        "dsr": psr(r, sr0),
    }


def min_track_record_length(returns: np.ndarray, sr_benchmark: float = 0.0,
                            confidence: float = 0.95) -> float:
    """Quantas observacoes seriam necessarias para o SR ser significativo."""
    r = np.asarray(returns, dtype="float64")
    r = r[np.isfinite(r)]
    if len(r) < 3:
        return np.inf
    sd = r.std(ddof=1)
    if sd <= 0:
        return np.inf
    sr = r.mean() / sd
    if sr <= sr_benchmark:
        return np.inf
    z = (r - r.mean()) / sd
    g3 = float((z ** 3).mean())
    g4 = float((z ** 4).mean())
    denom = (sr - sr_benchmark) ** 2
    return float(1.0 + (1.0 - g3 * sr + (g4 - 1.0) / 4.0 * sr ** 2)
                 * (norm.ppf(confidence) / (sr - sr_benchmark)) ** 2) if denom > 0 else np.inf


def bootstrap_ci(values: np.ndarray, stat=np.mean, n_boot: int = 5000,
                 alpha: float = 0.05, seed: int = 7) -> tuple[float, float]:
    rng = np.random.default_rng(seed)
    v = np.asarray(values, dtype="float64")
    v = v[np.isfinite(v)]
    if len(v) < 5:
        return (np.nan, np.nan)
    idx = rng.integers(0, len(v), size=(n_boot, len(v)))
    boots = stat(v[idx], axis=1)
    return (float(np.quantile(boots, alpha / 2)), float(np.quantile(boots, 1 - alpha / 2)))
