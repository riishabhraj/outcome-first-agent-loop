"""Lead-feature codec and the single shared cohort ("slice") object (C2).

A Slice is a feature predicate: AND across features, OR within a feature's value list - exactly the simulator's
`where` clause. Its id is canonical (sorted), so the same population always has the same id wherever it is
referenced: detection, experiments, the config sent to the dialler, the supervisor view and the audit store.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

FEATURES = {
    "lang": ["hi", "en", "ta", "te", "kn", "mr", "bn"],
    "city_tier": ["1", "2", "3"],
    "age_band": ["18-30", "31-45", "46-60", "60+"],
    "value_band": ["low", "mid", "high"],
}
FEAT_NAMES = list(FEATURES)
_IDX = {f: {v: i for i, v in enumerate(vs)} for f, vs in FEATURES.items()}
SHIFT = {"lang": 0, "city_tier": 3, "age_band": 5, "value_band": 7}
NCELL = 512

# decode tables over all 512 packed cells
_codes = np.arange(NCELL)
DECODE = {
    "lang": _codes & 7,
    "city_tier": (_codes >> 3) & 3,
    "age_band": (_codes >> 5) & 3,
    "value_band": (_codes >> 7) & 3,
}
VALID = (DECODE["lang"] < 7) & (DECODE["city_tier"] < 3) & (DECODE["value_band"] < 3)


def pack(features: dict) -> int:
    return (_IDX["lang"][features["lang"]] | (_IDX["city_tier"][features["city_tier"]] << 3)
            | (_IDX["age_band"][features["age_band"]] << 5) | (_IDX["value_band"][features["value_band"]] << 7))


def pcell(cell):
    """(lang, tier, age) sub-cell used by the pickup tables (value band does not drive pickup)."""
    return (cell & 7) | (((cell >> 3) & 3) << 3) | (((cell >> 5) & 3) << 5)


@dataclass(frozen=True)
class Slice:
    where: tuple  # ((feature, (values...)), ...) sorted

    @staticmethod
    def make(where: dict) -> "Slice":
        return Slice(tuple(sorted((f, tuple(sorted(vs, key=FEATURES[f].index))) for f, vs in where.items())))

    @property
    def id(self) -> str:
        return "ALL" if not self.where else "|".join(f"{f}={','.join(vs)}" for f, vs in self.where)

    def as_where(self) -> dict:
        return {f: list(vs) for f, vs in self.where}

    def mask(self) -> np.ndarray:
        """Boolean mask over the 512 packed cells."""
        m = VALID.copy()
        for f, vs in self.where:
            m &= np.isin(DECODE[f], [_IDX[f][v] for v in vs])
        return m

    def matches(self, cell: int) -> bool:
        return bool(self.mask()[cell])


def parse_slice_id(sid: str) -> Slice:
    if sid == "ALL":
        return Slice(())
    return Slice.make({p.split("=")[0]: p.split("=")[1].split(",") for p in sid.split("|")})
