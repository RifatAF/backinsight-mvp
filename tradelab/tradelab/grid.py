"""The 300-config grid (10 bases x 5 vol targets x 3 regime filters x 2 funding filters) and plateau neighbours."""
from itertools import product

import numpy as np

from tradelab.strategies import BASES, FAMILIES

V_GRID = [None, 0.10, 0.15, 0.20, 0.25]
P_GRID = [None, 0.80, 0.90]
Q_GRID = [None, 0.90]

# axis orders for +-1 steps: windows within family (fast -> slow), numeric v, p with "none" = no threshold
V_AXIS = [0.10, 0.15, 0.20, 0.25]
P_AXIS = [0.80, 0.90, None]


def configs():
    return [{"base": b, "v": v, "p": p, "q": q} for b, v, p, q in product(BASES, V_GRID, P_GRID, Q_GRID)]


def config_id(c):
    return f"{c['base']}|v={c['v']}|p={c['p']}|q={c['q']}"


def _steps(axis, value):
    if value not in axis:
        return []
    i = axis.index(value)
    return [axis[j] for j in (i - 1, i + 1) if 0 <= j < len(axis)]


def neighbors(confs):
    """Index lists of configs one step away on the window, v or p axis (same family, same q)."""
    pos = {config_id(c): i for i, c in enumerate(confs)}
    fam_of = {b: fam for fam, bs in FAMILIES.items() for b in bs}
    out = []
    for c in confs:
        cand = [dict(c, base=b) for b in _steps(FAMILIES[fam_of[c["base"]]], c["base"])]
        cand += [dict(c, v=v) for v in _steps(V_AXIS, c["v"])]
        cand += [dict(c, p=p) for p in _steps(P_AXIS, c["p"])]
        out.append(np.array([pos[config_id(x)] for x in cand]))
    return out
