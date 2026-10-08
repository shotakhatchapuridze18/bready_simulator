"""
B-READY 2025 Custom Index & Policy Impact Simulator
===================================================

Builds a single composite rating from the World Bank's Business Ready 2025
data (which deliberately publishes no overall economy score), and simulates
how indicator-level regulatory changes propagate to that rating and to an
economy's implied global rank.

Data model
----------
The published hierarchy is strictly ADDITIVE at every level below the pillar:

    indicator points  ->  sum  ->  subcategory
    subcategory       ->  sum  ->  category
    category          ->  sum  ->  pillar          (pillar is scored /100)
    pillar            ->  mean ->  topic           (equal weights by default)
    topic             ->  mean ->  composite       (equal weights by default)

This is verified on load: reconstructing each pillar from its indicator
columns must reproduce the published pillar score. Because the tree is
additive, a reform simulation is exact arithmetic, not an estimate.

Ceilings
--------
The dataset does not publish maximum attainable points per indicator. Two
reference ceilings are derived instead:

  * "frontier"  - the maximum value observed across the 101 economies.
                  Empirically grounded and defensible: it is a level some
                  economy has actually achieved. This is the DEFAULT.
  * "handbook"  - user-supplied true maxima from the Methodology Handbook,
                  passed via `set_ceilings()`. Use when available.

Note that summing per-indicator frontier values can exceed 100 within a
pillar, because point allocations inside some subcategories vary by economy
context. Frontier ceilings are therefore applied per indicator, never summed
to claim an attainable pillar total. `frontier_gap_report()` flags this.

Author: built for PMCG
Source: B-READY 2025 public data files, World Bank Group (CC BY 4.0)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

TOPIC_SHEETS = {
    "Business Entry": "01_Business_Entry",
    "Business Location": "02_Business_Location",
    "Utility Services": "03_Utility_Services",
    "Labor": "04_Labor",
    "Financial Services": "05_Financial_Services",
    "International Trade": "06_International_Trade",
    "Taxation": "07_Taxation",
    "Dispute Resolution": "08_Dispute_Resolution",
    "Market Competition": "09_Market_Competition",
    "Business Insolvency": "10_Business_Insolvency",
}

PILLAR_NAMES = {
    1: "Regulatory Framework",
    2: "Public Services",
    3: "Operational Efficiency",
}

# Peer groups for regional comparison. Only economies covered by B-READY 2025 appear.
# World Bank regions / income groups come from regions.csv (WDI country metadata).
CUSTOM_GROUPS = {
    "South Caucasus": ["Armenia", "Azerbaijan", "Georgia"],
    "Central Asia": ["Kazakhstan", "Kyrgyz Republic", "Tajikistan", "Turkmenistan", "Uzbekistan"],
    "Middle East": ["Bahrain", "Israel", "Jordan", "West Bank and Gaza"],
    "Eastern Partnership": ["Armenia", "Azerbaijan", "Georgia", "Moldova"],
    "Western Balkans": ["Bosnia and Herzegovina", "Montenegro", "North Macedonia", "Serbia"],
    "EU members": ["Belgium", "Bulgaria", "Croatia", "Cyprus", "Czechia", "Estonia", "Greece",
                   "Hungary", "Ireland", "Italy", "Latvia", "Malta", "Poland", "Portugal",
                   "Romania", "Slovak Republic", "Slovenia", "Spain", "Sweden"],
}
GROUPINGS = ("Neighbourhoods", "World Bank region", "Income group")

# Default rating bands. Calibrate against the realised distribution before
# showing these to a client -- see BReadyModel.suggest_bands().
DEFAULT_BANDS = [
    (80.0, "AAA"), (75.0, "AA"), (70.0, "A"), (65.0, "BBB"),
    (60.0, "BB"), (55.0, "B"), (45.0, "CCC"), (0.0, "D"),
]


# --------------------------------------------------------------------------
# Column metadata
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Column:
    """One column of a topic sheet, with its position in the hierarchy."""
    topic: str
    pillar: int
    category: Optional[str]
    subcategory: Optional[str]
    label: str
    level: str          # 'topic' | 'pillar' | 'category' | 'subcategory' | 'indicator'
    key: int            # positional index in the topic frame

    @property
    def uid(self) -> str:
        return f"{self.topic}|{self.pillar}|{self.subcategory or '-'}|{self.label}"


def _classify(l0, l1, l2, l3, topic: str, key: int) -> Optional[Column]:
    """Map a 4-row header tuple onto a Column, or None for identifier columns."""
    label = "" if pd.isna(l3) else str(l3).strip()
    if label in ("Economy", "Economy Code"):
        return None

    def clean(x):
        return None if (x is None or pd.isna(x) or str(x).startswith("Unnamed")) else str(x).strip()

    l0, l1, l2 = clean(l0), clean(l1), clean(l2)

    if l0 is None:                       # "<Topic> Overall"
        return Column(topic, 0, None, None, label, "topic", key)

    m = re.match(r"Pillar\s*(\d)", l0)
    pillar = int(m.group(1)) if m else 0

    if l1 is None:
        level = "pillar"
    elif l2 is None:
        level = "category"
    elif label.startswith("Subcategory"):
        level = "subcategory"
    else:
        level = "indicator"

    return Column(topic, pillar, l1, l2, label, level, key)


# --------------------------------------------------------------------------
# Scenario
# --------------------------------------------------------------------------
@dataclass
class Scenario:
    """A named set of indicator overrides for one economy."""
    name: str
    economy: str
    # (topic, indicator_label) -> new point value
    changes: Dict[tuple, float] = field(default_factory=dict)

    def set(self, topic: str, indicator: str, points: float) -> "Scenario":
        self.changes[(topic, indicator)] = float(points)
        return self

    def __len__(self) -> int:
        return len(self.changes)


# --------------------------------------------------------------------------
# Model
# --------------------------------------------------------------------------
class BReadyModel:
    """Composite index and policy simulator over B-READY 2025."""

    def __init__(self, scores_path: str, answers_path: Optional[str] = None,
                 verify: bool = True, maxima_path: Optional[str] = None):
        self.scores_path = scores_path
        self.answers_path = answers_path
        self.frames: Dict[str, pd.DataFrame] = {}
        self.columns: Dict[str, List[Column]] = {}
        self.economies: List[str] = []
        self._answers: Dict[str, pd.DataFrame] = {}
        self._ceilings: Dict[tuple, float] = {}
        # per-economy maxima from the 101 profile PDFs (economies x column key)
        self.maxima_path = maxima_path
        self._max: Dict[str, pd.DataFrame] = {}
        self._na: Dict[str, pd.DataFrame] = {}

        self._load()
        if maxima_path:
            self._load_maxima(maxima_path)
        if verify:
            self.verification = self.verify_additivity()

    # -------------------------------------------------- loading
    def _load(self) -> None:
        for topic, sheet in TOPIC_SHEETS.items():
            df = pd.read_excel(self.scores_path, sheet_name=sheet, header=[0, 1, 2, 3])
            df = df.rename(columns=lambda c: c)
            economies = df.iloc[:, 0].astype(str).str.strip()

            cols: List[Column] = []
            for key, hdr in enumerate(df.columns):
                col = _classify(hdr[0], hdr[1], hdr[2], hdr[3], topic, key)
                if col is not None:
                    cols.append(col)

            # Same indicator name twice in a topic (Business Location: "Online Permitting
            # Systems..." for building AND environmental permits). Scenarios address indicators by
            # name, so add the subcategory to make each one selectable on its own.
            seen: Dict[str, int] = {}
            for c in cols:
                if c.level == "indicator":
                    seen[c.label] = seen.get(c.label, 0) + 1
            cols = [Column(c.topic, c.pillar, c.category, c.subcategory,
                           f"{c.label} ({re.sub(r'^[0-9. ]+', '', c.subcategory or '')})", c.level, c.key)
                    if c.level == "indicator" and seen[c.label] > 1 else c
                    for c in cols]

            body = df.copy()
            body.columns = range(len(df.columns))
            body.index = pd.Index(economies.values, name="economy")
            body = body.drop(columns=[0, 1], errors="ignore")

            self.frames[topic] = body.astype(float)
            self.columns[topic] = cols

            if not self.economies:
                self.economies = sorted(economies.tolist())

        # frontier ceilings: max observed per indicator
        for topic, cols in self.columns.items():
            f = self.frames[topic]
            for c in cols:
                if c.level == "indicator":
                    self._ceilings[(topic, c.label)] = float(f[c.key].max())

    def _load_maxima(self, maxima_path: str) -> None:
        """Per-economy ceilings + N/A flags, joined to the workbook columns by name."""
        from bready_data_loader import load_joined
        j = load_joined(maxima_path, self.scores_path)
        name_of = {e.lower(): e for e in self.economies}
        j["economy_wb"] = j["key"].map(name_of)
        for topic in TOPIC_SHEETS:
            sub = j[j.topic == topic.upper()]
            self._max[topic] = sub.pivot(index="economy_wb", columns="col", values="max")
            self._na[topic] = sub.pivot(index="economy_wb", columns="col", values="not_applicable").astype(bool)

    def ceiling(self, economy: str, topic: str, col: Column) -> float:
        """This economy's real maximum for one indicator.

        With the maxima CSV: the economy's own maximum from its profile. If the economy
        already earns more than the printed maximum (8 profile cells, e.g. island Trade II),
        the earned value is used, so no reform can show a negative gap.
        Without it: the old frontier (best value observed across the 101 economies).
        """
        if self._max:
            mx = float(self._max[topic].loc[economy, col.key])
            return max(mx, float(self.frames[topic].loc[economy, col.key]))
        return self._ceilings[(topic, col.label)]

    def is_na(self, economy: str, topic: str, col: Column) -> bool:
        """True if the indicator does not apply to this economy (profile max = 0)."""
        return bool(self._na[topic].loc[economy, col.key]) if self._na else False

    def _cols(self, topic: str, level: str, pillar: Optional[int] = None) -> List[Column]:
        return [c for c in self.columns[topic]
                if c.level == level and (pillar is None or c.pillar == pillar)]

    # -------------------------------------------------- integrity
    def verify_additivity(self) -> pd.DataFrame:
        """Reconstruct every pillar from its indicators and compare to published."""
        rows = []
        for topic in TOPIC_SHEETS:
            f = self.frames[topic]
            for p in (1, 2, 3):
                pub_col = self._cols(topic, "pillar", p)
                ind = self._cols(topic, "indicator", p)
                if not pub_col or not ind:
                    continue
                pub = f[pub_col[0].key]
                rec = f[[c.key for c in ind]].sum(axis=1)
                rows.append({
                    "topic": topic,
                    "pillar": p,
                    "n_indicators": len(ind),
                    "max_abs_error": float((rec - pub).abs().max()),
                })
        return pd.DataFrame(rows)

    # -------------------------------------------------- scoring
    def pillar_scores(self, overrides: Optional[Dict[tuple, float]] = None) -> pd.DataFrame:
        """Economies x (topic, pillar) matrix of pillar scores, 0-100."""
        overrides = overrides or {}
        out = {}
        for topic in TOPIC_SHEETS:
            f = self.frames[topic]
            for p in (1, 2, 3):
                ind = self._cols(topic, "indicator", p)
                if not ind:
                    continue
                sub = f[[c.key for c in ind]].copy()
                sub.columns = [c.label for c in ind]
                for (t, lab), val in overrides.items():
                    if t == topic and lab in sub.columns:
                        sub[lab] = val
                out[(topic, p)] = sub.sum(axis=1)
        df = pd.DataFrame(out)
        df.columns = pd.MultiIndex.from_tuples(df.columns, names=["topic", "pillar"])
        return df

    def topic_scores(self, pillar_weights: Optional[Dict[int, float]] = None,
                     overrides: Optional[Dict[tuple, float]] = None) -> pd.DataFrame:
        """Economies x topic matrix. Default: equal pillar weights."""
        pw = pillar_weights or {1: 1 / 3, 2: 1 / 3, 3: 1 / 3}
        tot = sum(pw.values())
        pw = {k: v / tot for k, v in pw.items()}
        ps = self.pillar_scores(overrides)
        out = {}
        for topic in TOPIC_SHEETS:
            acc = 0.0
            for p, w in pw.items():
                if (topic, p) in ps.columns:
                    acc = acc + ps[(topic, p)] * w
            out[topic] = acc
        return pd.DataFrame(out)

    def composite(self, topic_weights: Optional[Dict[str, float]] = None,
                  pillar_weights: Optional[Dict[int, float]] = None,
                  overrides: Optional[Dict[tuple, float]] = None) -> pd.Series:
        """Single 0-100 composite per economy. Default: equal topic weights."""
        ts = self.topic_scores(pillar_weights, overrides)
        tw = topic_weights or {t: 1.0 for t in ts.columns}
        w = pd.Series({t: tw.get(t, 0.0) for t in ts.columns}, dtype=float)
        if w.sum() == 0:
            raise ValueError("Topic weights sum to zero.")
        w = w / w.sum()
        return (ts * w).sum(axis=1).rename("composite")

    # -------------------------------------------------- ratings & ranks
    @staticmethod
    def rate(score: float, bands: Sequence = DEFAULT_BANDS) -> str:
        for cut, letter in sorted(bands, key=lambda x: -x[0]):
            if score >= cut:
                return letter
        return bands[-1][1]

    def suggest_bands(self, n: int = 8, **kw) -> List[tuple]:
        """Quantile-calibrated bands over the realised 101-economy distribution."""
        c = self.composite(**kw)
        qs = np.linspace(0, 1, n + 1)[1:-1]
        cuts = sorted(c.quantile(qs).tolist(), reverse=True)
        letters = ["AAA", "AA", "A", "BBB", "BB", "B", "CCC", "D"][:n]
        return list(zip(cuts + [0.0], letters))

    def league_table(self, bands: Sequence = DEFAULT_BANDS, **kw) -> pd.DataFrame:
        c = self.composite(**kw).sort_values(ascending=False)
        return pd.DataFrame({
            "rank": range(1, len(c) + 1),
            "composite": c.round(2).values,
            "rating": [self.rate(v, bands) for v in c.values],
        }, index=c.index)

    # -------------------------------------------------- simulation
    def simulate(self, scenario: Scenario, bands: Sequence = DEFAULT_BANDS,
                 topic_weights=None, pillar_weights=None) -> Dict:
        """Run a scenario for one economy. Only that economy is perturbed."""
        e = scenario.economy
        kw = dict(topic_weights=topic_weights, pillar_weights=pillar_weights)

        base_c = self.composite(**kw)
        base_t = self.topic_scores(pillar_weights)
        base_p = self.pillar_scores()

        # apply overrides to the target economy only
        sim_frames = {t: f.copy() for t, f in self.frames.items()}
        touched = set()
        for (topic, label), val in scenario.changes.items():
            match = [c for c in self.columns[topic]
                     if c.level == "indicator" and c.label == label]
            if not match:
                raise KeyError(f"No indicator '{label}' in topic '{topic}'.")
            sim_frames[topic].loc[e, match[0].key] = val
            touched.add(topic)

        saved = self.frames
        try:
            self.frames = sim_frames
            sim_c = self.composite(**kw)
            sim_t = self.topic_scores(pillar_weights)
            sim_p = self.pillar_scores()
        finally:
            self.frames = saved

        base_rank = int(base_c.rank(ascending=False, method="min")[e])
        sim_rank = int(sim_c.rank(ascending=False, method="min")[e])

        topic_tbl = pd.DataFrame({
            "baseline": base_t.loc[e], "simulated": sim_t.loc[e]
        })
        topic_tbl["delta"] = topic_tbl.simulated - topic_tbl.baseline

        pillar_rows = []
        for topic in sorted(touched):
            for p in (1, 2, 3):
                if (topic, p) in base_p.columns:
                    pillar_rows.append({
                        "topic": topic, "pillar": f"P{p} {PILLAR_NAMES[p]}",
                        "baseline": base_p.loc[e, (topic, p)],
                        "simulated": sim_p.loc[e, (topic, p)],
                        "delta": sim_p.loc[e, (topic, p)] - base_p.loc[e, (topic, p)],
                    })

        return {
            "scenario": scenario.name,
            "economy": e,
            "n_changes": len(scenario),
            "baseline_score": float(base_c[e]),
            "simulated_score": float(sim_c[e]),
            "delta": float(sim_c[e] - base_c[e]),
            "baseline_rating": self.rate(float(base_c[e]), bands),
            "simulated_rating": self.rate(float(sim_c[e]), bands),
            "baseline_rank": base_rank,
            "simulated_rank": sim_rank,
            "rank_change": base_rank - sim_rank,
            "topics": topic_tbl.round(3),
            "pillars": pd.DataFrame(pillar_rows).round(3),
        }

    # -------------------------------------------------- gap analysis
    def frontier_gap_report(self, economy: str, topic: Optional[str] = None,
                            top: int = 20) -> pd.DataFrame:
        """Indicators ranked by points between this economy and the frontier."""
        rows = []
        topics = [topic] if topic else list(TOPIC_SHEETS)
        for t in topics:
            f = self.frames[t]
            for c in self._cols(t, "indicator"):
                if self.is_na(economy, t, c):
                    continue
                cur = float(f.loc[economy, c.key])
                ceil = self.ceiling(economy, t, c)
                best = f[c.key].idxmax()
                if ceil - cur > 1e-9:
                    rows.append({
                        "topic": t, "pillar": f"P{c.pillar}",
                        "subcategory": c.subcategory, "indicator": c.label,
                        "current": round(cur, 3), "ceiling": round(ceil, 3),
                        "gap": round(ceil - cur, 3),
                        "frontier": round(self._ceilings[(t, c.label)], 3),
                        "frontier_held_by": best,
                    })
        df = pd.DataFrame(rows)
        if df.empty:
            return df
        return df.sort_values("gap", ascending=False).head(top).reset_index(drop=True)

    def set_ceilings(self, ceilings: Dict[tuple, float]) -> None:
        """Override frontier ceilings with true handbook maxima."""
        self._ceilings.update({k: float(v) for k, v in ceilings.items()})

    def max_out(self, economy: str, topic: str, pillar: Optional[int] = None,
                name: str = "Reach frontier") -> Scenario:
        """Scenario that lifts every indicator to the frontier ceiling."""
        s = Scenario(name, economy)
        f = self.frames[topic]
        for c in self._cols(topic, "indicator", pillar):
            if self.is_na(economy, topic, c):
                continue
            ceil = self.ceiling(economy, topic, c)
            if ceil > float(f.loc[economy, c.key]):
                s.set(topic, c.label, ceil)
        return s

    # -------------------------------------------------- benchmarking
    def benchmark(self, economies: Sequence[str], **kw) -> pd.DataFrame:
        lt = self.league_table(**kw)
        ts = self.topic_scores(kw.get("pillar_weights"))
        out = lt.loc[list(economies)].join(ts.loc[list(economies)].round(2))
        return out.sort_values("composite", ascending=False)

    def peers(self, economy: str, n: int = 5, **kw) -> pd.DataFrame:
        """Economies immediately above and below in the league table."""
        lt = self.league_table(**kw)
        i = lt.index.get_loc(economy)
        lo, hi = max(0, i - n), min(len(lt), i + n + 1)
        return lt.iloc[lo:hi]

    # -------------------------------------------------- regional comparison
    def load_regions(self, path: str) -> None:
        """economy -> World Bank region and income group (regions.csv)."""
        r = pd.read_csv(path)
        self.regions = r.set_index("economy")[["wb_region", "income_group"]]

    def groups(self, grouping: str) -> Dict[str, List[str]]:
        """{group name: [economies]} for one of GROUPINGS. An economy may sit in 2 neighbourhoods."""
        if grouping == "Neighbourhoods":
            return {g: [e for e in m if e in self.economies] for g, m in CUSTOM_GROUPS.items()}
        if not hasattr(self, "regions"):
            raise RuntimeError("Call load_regions() first.")
        col = "wb_region" if grouping == "World Bank region" else "income_group"
        s = self.regions[col].reindex(self.economies)
        return {g: sorted(s[s == g].index) for g in s.dropna().unique()}

    def group_table(self, grouping: str, topic_weights=None, pillar_weights=None) -> pd.DataFrame:
        """Simple (unweighted) average of composite and topic scores per group."""
        comp = self.composite(topic_weights, pillar_weights)
        ts = self.topic_scores(pillar_weights)
        rows = []
        for g, members in self.groups(grouping).items():
            if not members:
                continue
            row = {"group": g, "n": len(members), "composite": comp[members].mean()}
            row.update(ts.loc[members].mean().to_dict())
            rows.append(row)
        return pd.DataFrame(rows).set_index("group").sort_values("composite", ascending=False)

    def groups_of(self, economy: str, grouping: str) -> List[str]:
        return [g for g, m in self.groups(grouping).items() if economy in m]

    # -------------------------------------------------- answers
    def answers(self, topic: str, economy: Optional[str] = None) -> pd.DataFrame:
        if not self.answers_path:
            raise RuntimeError("No answers workbook supplied.")
        if topic not in self._answers:
            self._answers[topic] = pd.read_excel(
                self.answers_path, sheet_name=TOPIC_SHEETS[topic])
        df = self._answers[topic]
        return df[df["Economy"] == economy] if economy else df

    def indicator_catalogue(self, topic: Optional[str] = None,
                            economy: Optional[str] = None) -> pd.DataFrame:
        """All indicators. With `economy`: adds its current points, own ceiling and N/A flag."""
        rows = []
        for t in ([topic] if topic else list(TOPIC_SHEETS)):
            for c in self._cols(t, "indicator"):
                r = {
                    "topic": t, "pillar": c.pillar,
                    "pillar_name": PILLAR_NAMES.get(c.pillar, ""),
                    "category": c.category, "subcategory": c.subcategory,
                    "indicator": c.label,
                    "frontier": round(self._ceilings[(t, c.label)], 3),
                }
                if economy is not None:
                    r["current"] = float(self.frames[t].loc[economy, c.key])
                    r["ceiling"] = self.ceiling(economy, t, c)
                    r["not_applicable"] = self.is_na(economy, t, c)
                rows.append(r)
        return pd.DataFrame(rows)


# --------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    scores = sys.argv[1] if len(sys.argv) > 1 else "01_B-READY-2025-PILLAR-TOPIC-SCORES.xlsx"
    answers = sys.argv[2] if len(sys.argv) > 2 else None
    maxima = sys.argv[3] if len(sys.argv) > 3 else None

    m = BReadyModel(scores, answers, maxima_path=maxima)
    print("Additivity check, worst error across all 30 pillars: "
          f"{m.verification.max_abs_error.max():.2e}")

    lt = m.league_table()
    print("\nTop 5 (equal weights):")
    print(lt.head(5).to_string())
    print(f"\nGeorgia: {lt.loc['Georgia'].to_dict()}")

    print("\nBiggest Taxation gaps (ceiling = Georgia's own maximum if maxima CSV given):")
    print(m.frontier_gap_report("Georgia", "Taxation", top=5).to_string(index=False))

    sc = (Scenario("Green tax package", "Georgia")
          .set("Taxation", "Presence of Environmental Fiscal Instruments", 6.667)
          .set("Taxation", "Availability of Tax Incentives", 4.444)
          .set("Taxation", "Availability of Public Consultations", 3.333)
          .set("Taxation", "Transition Periods", 3.333))
    r = m.simulate(sc)
    print(f"\n{r['scenario']}: {r['baseline_score']:.2f} -> {r['simulated_score']:.2f} "
          f"({r['delta']:+.2f}), rank {r['baseline_rank']} -> {r['simulated_rank']}")
