"""Statistics. Pure stdlib - no scipy, no statsmodels.

The three things that matter here and are usually got wrong elsewhere:

  1. The cluster is the TASK, not the trial. k replicates per task are not k
     independent observations. Every interval resamples tasks.
  2. "No difference" needs an equivalence test against a declared margin, not a
     non-significant p-value.
  3. Searching many candidates means many tests; without FDR control the
     discoveries are noise by construction.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass


# ------------------------------------------------------------------ basics


def mean(xs) -> float:
    xs = list(xs)
    return sum(xs) / len(xs) if xs else 0.0


def normal_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def wilson_interval(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - half) / d, (c + half) / d)


# ------------------------------------------------- cluster-aware estimation


@dataclass
class ArmEstimate:
    arm: str
    n_tasks: int
    n_trials: int
    rate: float               # cluster-mean of per-task rates
    ci: tuple[float, float]
    per_task: dict[str, float]

    def to_dict(self) -> dict:
        return {"arm": self.arm, "n_tasks": self.n_tasks, "n_trials": self.n_trials,
                "rate": round(self.rate, 4),
                "ci": [round(self.ci[0], 4), round(self.ci[1], 4)]}


def per_task_rates(obs: list[tuple[str, bool]]) -> dict[str, float]:
    """obs: [(task_id, success)] -> {task_id: rate}. This is the clustering."""
    agg: dict[str, list[int]] = {}
    for task, ok in obs:
        agg.setdefault(task, []).append(1 if ok else 0)
    return {t: sum(v) / len(v) for t, v in agg.items()}


def cluster_bootstrap_mean(rates: dict[str, float], iters: int = 5000,
                           seed: int = 12345) -> tuple[float, tuple[float, float]]:
    """Resample TASKS with replacement. The whole point."""
    tasks = list(rates)
    if not tasks:
        return 0.0, (0.0, 0.0)
    point = mean(rates.values())
    rng = random.Random(seed)
    draws = []
    for _ in range(iters):
        sample = [rates[rng.choice(tasks)] for _ in tasks]
        draws.append(mean(sample))
    draws.sort()
    lo = draws[int(0.025 * len(draws))]
    hi = draws[min(len(draws) - 1, int(0.975 * len(draws)))]
    return point, (lo, hi)


def estimate_arm(arm: str, obs: list[tuple[str, bool]], iters: int = 5000) -> ArmEstimate:
    rates = per_task_rates(obs)
    point, ci = cluster_bootstrap_mean(rates, iters=iters)
    return ArmEstimate(arm, len(rates), len(obs), point, ci, rates)


# --------------------------------------------------------- paired inference


@dataclass
class PairedResult:
    delta: float
    ci: tuple[float, float]
    p_value: float
    n_pairs: int
    method: str
    discordant: tuple[int, int] = (0, 0)

    def to_dict(self) -> dict:
        return {"delta": round(self.delta, 4),
                "ci": [round(self.ci[0], 4), round(self.ci[1], 4)],
                "p_value": round(self.p_value, 5),
                "n_pairs": self.n_pairs, "method": self.method,
                "discordant": list(self.discordant)}


def paired_cluster_bootstrap(a: dict[str, float], b: dict[str, float],
                             iters: int = 5000, seed: int = 999) -> PairedResult:
    """Paired on task. Resamples the shared task list, not the trials."""
    tasks = sorted(set(a) & set(b))
    if not tasks:
        return PairedResult(0.0, (0.0, 0.0), 1.0, 0, "cluster_bootstrap")
    diffs = {t: b[t] - a[t] for t in tasks}
    point = mean(diffs.values())
    rng = random.Random(seed)
    draws = []
    for _ in range(iters):
        sample = [diffs[rng.choice(tasks)] for _ in tasks]
        draws.append(mean(sample))
    draws.sort()
    lo = draws[int(0.025 * len(draws))]
    hi = draws[min(len(draws) - 1, int(0.975 * len(draws)))]
    # two-sided bootstrap p: proportion of draws on the other side of zero
    below = sum(1 for d in draws if d <= 0)
    above = len(draws) - below
    p = 2.0 * min(below, above) / len(draws)
    p = min(1.0, max(p, 1.0 / len(draws)))
    return PairedResult(point, (lo, hi), p, len(tasks), "cluster_bootstrap")


def mcnemar(a: dict[str, bool], b: dict[str, bool]) -> PairedResult:
    """Exact binomial McNemar on paired binary outcomes."""
    tasks = sorted(set(a) & set(b))
    n01 = sum(1 for t in tasks if not a[t] and b[t])     # b wins
    n10 = sum(1 for t in tasks if a[t] and not b[t])     # a wins
    n = n01 + n10
    delta = (n01 - n10) / len(tasks) if tasks else 0.0
    if n == 0:
        return PairedResult(0.0, (0.0, 0.0), 1.0, len(tasks), "mcnemar_exact", (n01, n10))
    k = min(n01, n10)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / (2 ** n)
    p = min(1.0, 2.0 * tail)
    lo, hi = wilson_interval(n01, n)
    span = (lo * n - n10) / len(tasks), (hi * n - n10) / len(tasks)
    return PairedResult(delta, span, p, len(tasks), "mcnemar_exact", (n01, n10))


# ------------------------------------------------------- equivalence (TOST)


@dataclass
class EquivalenceResult:
    equivalent: bool
    margin: float
    ci: tuple[float, float]

    def to_dict(self) -> dict:
        return {"equivalent": self.equivalent, "margin": self.margin,
                "ci": [round(self.ci[0], 4), round(self.ci[1], 4)]}


def tost(ci: tuple[float, float], margin: float) -> EquivalenceResult:
    """Equivalence iff the whole interval sits inside +/- margin.

    A non-significant p-value is NOT evidence of equivalence; this is.
    """
    return EquivalenceResult(ci[0] > -margin and ci[1] < margin, margin, ci)


# --------------------------------------------------------- multiplicity


def benjamini_hochberg(pvals: list[float], alpha: float = 0.05) -> list[bool]:
    """Returns per-test rejection decisions at FDR = alpha."""
    n = len(pvals)
    if n == 0:
        return []
    order = sorted(range(n), key=lambda i: pvals[i])
    reject = [False] * n
    kmax = -1
    for rank, i in enumerate(order, start=1):
        if pvals[i] <= alpha * rank / n:
            kmax = rank
    for rank, i in enumerate(order, start=1):
        if rank <= kmax:
            reject[i] = True
    return reject


# ------------------------------------------------------------------ power


def mcnemar_power_n(delta: float, discordance: float, alpha: float = 0.05,
                    power: float = 0.80) -> int:
    """Tasks needed to detect `delta` given a discordance rate.

    This is the number the whole field skips. Surfacing it before the run is
    what stops the platform from buying an experiment that cannot answer its
    own question.
    """
    if delta <= 0 or discordance <= 0:
        return 0
    z_a = 1.959964 if alpha == 0.05 else _z(1 - alpha / 2)
    z_b = 0.8416212 if power == 0.80 else _z(power)
    inner = z_a * math.sqrt(discordance) + z_b * math.sqrt(
        max(1e-9, discordance - delta * delta)
    )
    return int(math.ceil((inner * inner) / (delta * delta)))


def minimum_detectable_effect(n_tasks: int, discordance: float = 0.25,
                              alpha: float = 0.05, power: float = 0.80) -> float:
    """Inverse of the above: what can this design actually see?"""
    lo, hi = 0.001, 0.999
    for _ in range(60):
        mid = (lo + hi) / 2
        if mcnemar_power_n(mid, discordance, alpha, power) > n_tasks:
            lo = mid
        else:
            hi = mid
    return round(hi, 4)


def _z(p: float) -> float:
    """Inverse normal CDF (Acklam-style rational approximation)."""
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    pl, ph = 0.02425, 1 - 0.02425
    if p < pl:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > ph:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
                ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
           (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


# ------------------------------------------------- anytime-valid stopping


def confidence_sequence_halfwidth(n: int, alpha: float = 0.05,
                                  var: float = 0.25) -> float:
    """Sub-Gaussian confidence sequence half-width.

    Valid under continuous monitoring, unlike a fixed-n interval. Peeking with
    a fixed-n p-value invalidates it; this is the legal way to stop early.
    """
    if n <= 1:
        return 1.0
    rho = 1.0
    inner = (n * rho + 1.0) / (rho * rho)
    return math.sqrt(2 * var * (n * rho + 1.0) / (n * n * rho) *
                     math.log(math.sqrt(inner) / alpha))
