"""Join per-economy maxima (from the 101 profile PDFs) to the official indicator scores (workbook).

Why not join by position: inside Business Location, Labor, Trade and Market Competition the profile and
the workbook list some indicators in a different order. Names are the safer key, so:
  1. normalise names (lower-case, letters+digits only);
  2. number repeated names (duplicates exist) -> key = (topic, name, occurrence);
  3. the handful of names that still differ are paired by closest spelling (done once per topic,
     then applied to all economies). The result is checked against the earned points.
"""
import difflib
import re

import pandas as pd

SHEETS = {"BUSINESS ENTRY": "01_Business_Entry", "BUSINESS LOCATION": "02_Business_Location",
          "UTILITY SERVICES": "03_Utility_Services", "LABOR": "04_Labor",
          "FINANCIAL SERVICES": "05_Financial_Services", "INTERNATIONAL TRADE": "06_International_Trade",
          "TAXATION": "07_Taxation", "DISPUTE RESOLUTION": "08_Dispute_Resolution",
          "MARKET COMPETITION": "09_Market_Competition", "BUSINESS INSOLVENCY": "10_Business_Insolvency"}


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def _keyed(names):
    s = pd.Series([_norm(n) for n in names])
    return list(zip(s, s.groupby(s).cumcount()))


def load_joined(maxima_csv, scores_xlsx):
    mx = pd.read_csv(maxima_csv)
    mx = mx[mx.kind == "ind"].copy()
    mx["key"] = mx["economy"].str.lower()                      # "Lao Pdr" (profile) vs "Lao PDR" (workbook)
    mx["n"] = mx["name"].map(_norm)
    mx["occ"] = mx.groupby(["key", "topic", "n"]).cumcount()

    wb_parts = []
    for topic, sheet in SHEETS.items():
        sc = pd.read_excel(scores_xlsx, sheet_name=sheet, header=3)
        cols = [c for c in sc.columns[2:] if "Overall" not in str(c)]
        wb_keys = _keyed(cols)

        # map profile (name, occ) -> workbook (name, occ); exact first, closest spelling for leftovers
        ref = mx[(mx.topic == topic) & (mx.key == "georgia")]
        prof_keys = list(zip(ref.n, ref.occ))
        left_p = [k for k in prof_keys if k not in set(wb_keys)]
        left_w = [k for k in wb_keys if k not in set(prof_keys)]
        remap = {}
        for p in left_p:
            if not left_w:
                break
            best = max(left_w, key=lambda w: difflib.SequenceMatcher(None, p[0], w[0]).ratio())
            remap[p] = best
            left_w.remove(best)

        # read columns by position (iloc) so duplicate column names are harmless
        idx = [i for i, c in enumerate(sc.columns) if i >= 2 and "Overall" not in str(c)]
        rows = []
        for (nk, col_i) in zip(wb_keys, idx):
            rows.append(pd.DataFrame({"key": sc["Economy"].str.lower().values, "topic": topic,
                                      "n": nk[0], "occ": nk[1], "col": col_i, "wb_points": sc.iloc[:, col_i].values}))
        wb = pd.concat(rows, ignore_index=True)
        inv = {w: p for p, w in remap.items()}                  # workbook key -> profile key
        wb[["n", "occ"]] = [inv.get((a, b), (a, b)) for a, b in zip(wb.n, wb.occ)]
        wb_parts.append(wb)

    wb = pd.concat(wb_parts, ignore_index=True)
    return mx.merge(wb, on=["key", "topic", "n", "occ"], how="outer", indicator=True)


if __name__ == "__main__":
    df = load_joined("/home/claude/outputs/bready_2025_indicator_maxima.csv",
                     "/mnt/user-data/uploads/01_B-READY-2025-PILLAR-TOPIC-SCORES.xlsx")
    print(df["_merge"].value_counts().to_dict())
    both = df[df._merge == "both"]
    gap = (both.obtained - both.wb_points).abs()
    print("cells:", len(both), "| gap > 0.01:", int((gap > 0.01).sum()), "| max gap:", round(gap.max(), 3))
