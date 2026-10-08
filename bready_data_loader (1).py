"""Join per-economy maxima (from the 101 profile PDFs) to the official indicator scores (workbook).

Why not join by position: inside Business Location, Labor, Trade and Market Competition the profile and
the workbook list some indicators in a different order. Names are the safer key, so:
  1. normalise names (lower-case, letters+digits only);
  2. number repeated names (duplicates exist) -> key = (topic, name, occurrence);
  3. the handful of names that still differ are paired by closest spelling (done once per topic,
     then applied to all economies). The result is checked against the earned points.

A few maxima PRINTED in the profiles are wrong (the scores themselves are right). `fix_maxima()`
corrects them so every economy's pillar maxima add up to 100 again. See CORRECTIONS below.
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


# Profile printing errors, verified against the published scores and the answers workbook.
# (economy, topic, pillar, indicator name) -> true maximum points
_TT_IMPORT = 25 * 100 / 70     # export category (30 pts) missing -> spread over the other 70 pts
CORRECTIONS = {
    # Tonga: tax dispute time is -777 (no data) -> its 10 pts are spread over the other 4 indicators
    # of the category (12.5 each). Printed as 10 / 10, which makes earned 11.30 > max 10.
    ("Tonga", "TAXATION", "III", "Total Time for Preparation, Filing and Payment"): 12.5,
    ("Tonga", "TAXATION", "III", "The percentage of Firms Filing and Paying Taxes Electronically"): 12.5,
    ("Tonga", "TAXATION", "III", "Total Time Needed to Complete the Audit"): 12.5,
    ("Tonga", "TAXATION", "III", "Obtaining a VAT Refund in Practice"): 12.5,
    ("Tonga", "TAXATION", "III", "Time to Review a Tax Dispute"): 0.0,
    # Trinidad and Tobago: export data missing; import time/cost printed as 57.14 each (sum 142.85).
    ("Trinidad And Tobago", "INTERNATIONAL TRADE", "III", "Total Time to Comply with Import Requirements"): _TT_IMPORT,
    ("Trinidad And Tobago", "INTERNATIONAL TRADE", "III", "Total Cost to Comply with Import Requirements"): _TT_IMPORT,
}
TRUSTED_TRADER = "Availability of a Trusted Trader Program for Other Operators"


def fix_maxima(mx: pd.DataFrame) -> pd.DataFrame:
    """Correct printed maxima. Adds `max_printed` and `max_corrected` columns."""
    mx = mx.copy()
    mx["max_printed"] = mx["max"]
    # 16 island economies, Trade Pillar II: the trusted-trader indicator is printed with max 0 but is
    # scored (7 islands earn 2.78 on it). Restoring its standard 2.78 brings the pillar back to 100.
    pill = mx.groupby(["economy", "topic", "pillar"])["max"].transform("sum")
    isl = ((mx.topic == "INTERNATIONAL TRADE") & (mx.pillar == "II") & (mx.name == TRUSTED_TRADER)
           & (mx["max"] == 0) & ((pill + mx.std_max - 100).abs() < 0.1))
    mx.loc[isl, "max"] = mx.loc[isl, "std_max"]
    for (e, t, p, n), v in CORRECTIONS.items():
        hit = (mx.economy == e) & (mx.topic == t) & (mx.pillar == p) & (mx.name.str.strip() == n)
        assert hit.sum() == 1, (e, t, n)
        mx.loc[hit, "max"] = v
    mx["max_corrected"] = (mx["max"] - mx["max_printed"]).abs() > 1e-9
    mx["not_applicable"] = (mx["max"] == 0) & (mx["std_max"] > 0)
    return mx


def load_joined(maxima_csv, scores_xlsx):
    mx = pd.read_csv(maxima_csv)
    mx = fix_maxima(mx[mx.kind == "ind"])
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
    df = load_joined("bready_2025_indicator_maxima.csv",
                     "01_B-READY-2025-PILLAR-TOPIC-SCORES.xlsx")
    print(df["_merge"].value_counts().to_dict())
    both = df[df._merge == "both"]
    gap = (both.obtained - both.wb_points).abs()
    print("cells:", len(both), "| gap > 0.01:", int((gap > 0.01).sum()), "| max gap:", round(gap.max(), 3))
    sums = both.groupby(["economy", "topic", "pillar"])["max"].sum()
    print("pillars whose maxima don't add to 100:", int(((sums - 100).abs() > 0.1).sum()),
          "| earned > max:", int((both.obtained > both["max"] + 0.01).sum()),
          "| corrected cells:", int(both.max_corrected.sum()))
