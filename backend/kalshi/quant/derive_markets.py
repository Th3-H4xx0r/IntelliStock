"""Derive per-market probabilities from a Dixon-Coles scoreline matrix (pure)."""
from __future__ import annotations


def one_x_two(m: list[list[float]]) -> dict:
    n = len(m)
    home = sum(m[i][j] for i in range(n) for j in range(n) if i > j)
    draw = sum(m[i][i] for i in range(n))
    away = sum(m[i][j] for i in range(n) for j in range(n) if i < j)
    return {"home": home, "draw": draw, "away": away}


def over_under(m: list[list[float]], line: float) -> dict:
    n = len(m)
    over = sum(m[i][j] for i in range(n) for j in range(n) if (i + j) > line)
    return {"over": over, "under": 1.0 - over}


def btts(m: list[list[float]]) -> dict:
    n = len(m)
    yes = sum(m[i][j] for i in range(n) for j in range(n) if i >= 1 and j >= 1)
    return {"yes": yes, "no": 1.0 - yes}


def exact_score(m: list[list[float]], i: int, j: int) -> float:
    if 0 <= i < len(m) and 0 <= j < len(m):
        return m[i][j]
    return 0.0


def double_chance(m: list[list[float]]) -> dict:
    p = one_x_two(m)
    return {
        "home_draw": p["home"] + p["draw"],
        "home_away": p["home"] + p["away"],
        "draw_away": p["draw"] + p["away"],
    }
