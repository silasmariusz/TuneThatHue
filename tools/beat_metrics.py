"""
Beat-tracking evaluation metrics (mir_eval-style, local implementation).

All timestamps are integer microseconds. No dependencies beyond stdlib; kept
separate from the bench CLI so pytest can import and unit-test each metric.
"""

from __future__ import annotations

TOL_US = 70_000  # standard +/-70 ms beat-matching tolerance


def match_beats(est: list[int], ref: list[int], tol_us: int = TOL_US) -> list[tuple[int, int]]:
    """Greedy 1:1 matching of sorted estimate beats to reference beats."""
    pairs: list[tuple[int, int]] = []
    i = j = 0
    while i < len(est) and j < len(ref):
        d = est[i] - ref[j]
        if abs(d) <= tol_us:
            pairs.append((i, j))
            i += 1
            j += 1
        elif d < 0:
            i += 1
        else:
            j += 1
    return pairs


def f_measure(est: list[int], ref: list[int], tol_us: int = TOL_US) -> dict[str, float]:
    """Precision / recall / F-measure at the given tolerance."""
    if not est or not ref:
        return {"precision": 0.0, "recall": 0.0, "f": 0.0}
    tp = len(match_beats(est, ref, tol_us))
    precision = tp / len(est)
    recall = tp / len(ref)
    f = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {"precision": precision, "recall": recall, "f": f}


def _variant_grids(ref: list[int]) -> dict[str, list[int]]:
    """Metrical variants admitted by AML: half/double tempo and the offbeat."""
    out: dict[str, list[int]] = {"ref": list(ref)}
    if len(ref) < 3:
        return out
    out["double"] = sorted(ref + [(a + b) // 2 for a, b in zip(ref, ref[1:])])
    out["half_even"] = ref[::2]
    out["half_odd"] = ref[1::2]
    out["offbeat"] = [(a + b) // 2 for a, b in zip(ref, ref[1:])]
    return out


def _continuity_against(est: list[int], grid: list[int], tol: float) -> list[bool]:
    """Per-estimate-beat correctness: phase AND period within ``tol`` of the grid."""
    ok = [False] * len(est)
    if len(grid) < 2 or len(est) < 2:
        return ok
    j = 0
    for i, e in enumerate(est):
        while j + 1 < len(grid) and abs(grid[j + 1] - e) <= abs(grid[j] - e):
            j += 1
        ibi_ref = (
            grid[j + 1] - grid[j] if j + 1 < len(grid) else grid[j] - grid[j - 1] if j > 0 else 0
        )
        if ibi_ref <= 0:
            continue
        phase_ok = abs(grid[j] - e) <= tol * ibi_ref
        if i > 0:
            ibi_est = est[i] - est[i - 1]
            period_ok = abs(ibi_est - ibi_ref) <= tol * ibi_ref
        else:
            period_ok = True
        ok[i] = phase_ok and period_ok
    return ok


def continuity(est: list[int], ref: list[int], tol: float = 0.175) -> dict[str, float]:
    """
    CML/AML continuity scores.

    CMLt / AMLt: fraction of estimate beats correct (total); CMLc / AMLc: length
    of the longest continuously-correct run over the reference beat count.
    """
    if not est or len(ref) < 2:
        return {"cmlc": 0.0, "cmlt": 0.0, "amlc": 0.0, "amlt": 0.0}

    def _scores(ok: list[bool]) -> tuple[float, float]:
        total = sum(ok) / len(ref)
        best = cur = 0
        for good in ok:
            cur = cur + 1 if good else 0
            best = max(best, cur)
        return best / len(ref), total

    cmlc, cmlt = _scores(_continuity_against(est, ref, tol))
    amlc, amlt = cmlc, cmlt
    for grid in _variant_grids(ref).values():
        c, t = _scores(_continuity_against(est, grid, tol))
        if t > amlt:
            amlc, amlt = c, t
    return {
        "cmlc": min(1.0, cmlc),
        "cmlt": min(1.0, cmlt),
        "amlc": min(1.0, amlc),
        "amlt": min(1.0, amlt),
    }


def ibi_stats(est: list[int]) -> dict[str, float]:
    """Median inter-beat interval + duplicate / skip rates (the S1/S3 detectors)."""
    if len(est) < 3:
        return {"median_ibi_us": 0.0, "duplicate_rate": 0.0, "skip_rate": 0.0, "n_ibis": 0}
    ibis = sorted(b - a for a, b in zip(est, est[1:]))
    median = ibis[len(ibis) // 2]
    dup = sum(1 for x in ibis if x < 0.5 * median)
    skip = sum(1 for x in ibis if x > 1.5 * median)
    return {
        "median_ibi_us": float(median),
        "duplicate_rate": dup / len(ibis),
        "skip_rate": skip / len(ibis),
        "n_ibis": len(ibis),
    }


def tempo_verdict(bpm_est: float, bpm_truth: float, tol: float = 0.04) -> str:
    """Classify a tempo estimate against truth: ok / double / half / 3x / third / wrong."""
    if bpm_truth <= 0 or bpm_est <= 0:
        return "unknown"
    for name, mult in (("ok", 1.0), ("double", 2.0), ("half", 0.5), ("3x", 3.0), ("third", 1 / 3)):
        if abs(bpm_est - bpm_truth * mult) <= tol * bpm_truth * mult:
            return name
    return "wrong"
