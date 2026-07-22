"""
B-READY Custom Index & Policy Simulator -- Streamlit dashboard.

Run:
    pip install streamlit pandas numpy openpyxl
    streamlit run app.py
"""

import os

import pandas as pd
import streamlit as st

from bready_simulator import (
    DEFAULT_BANDS, PILLAR_NAMES, TOPIC_SHEETS, BReadyModel, Scenario,
)

st.set_page_config(page_title="B-READY Simulator", layout="wide")

SCORES_DEFAULT = "01_B-READY-2025-PILLAR-TOPIC-SCORES.xlsx"
ANSWERS_DEFAULT = "02_B-READY-2025-EconomyAnswer.xlsx"


@st.cache_resource(show_spinner="Loading B-READY 2025 data...")
def load_model(scores: str, answers: str | None):
    return BReadyModel(scores, answers if answers and os.path.exists(answers) else None)


# --------------------------------------------------------------- sidebar
st.sidebar.title("B-READY Simulator")

scores_path = st.sidebar.text_input("Scores workbook", SCORES_DEFAULT)
answers_path = st.sidebar.text_input("Answers workbook", ANSWERS_DEFAULT)

if not os.path.exists(scores_path):
    st.error(f"Scores workbook not found at `{scores_path}`. Set the path in the sidebar.")
    st.stop()

model = load_model(scores_path, answers_path)

country = st.sidebar.selectbox(
    "Target economy", model.economies,
    index=model.economies.index("Georgia") if "Georgia" in model.economies else 0)

st.sidebar.markdown("---")
st.sidebar.subheader("Pillar weights")
pw = {}
for p in (1, 2, 3):
    pw[p] = st.sidebar.slider(f"P{p} {PILLAR_NAMES[p]}", 0.0, 1.0, 1 / 3, 0.01)
if sum(pw.values()) == 0:
    pw = {1: 1 / 3, 2: 1 / 3, 3: 1 / 3}

st.sidebar.subheader("Topic weights")
equal = st.sidebar.checkbox("Equal weights (recommended)", value=True)
tw = {}
for t in TOPIC_SHEETS:
    tw[t] = 0.1 if equal else st.sidebar.slider(t, 0.0, 0.3, 0.1, 0.01)

WKW = dict(topic_weights=tw, pillar_weights=pw)

st.sidebar.markdown("---")
if st.sidebar.checkbox("Calibrate rating bands to this distribution"):
    bands = model.suggest_bands(**WKW)
    st.sidebar.caption("Bands set by octile of the 101 economies.")
else:
    bands = DEFAULT_BANDS

# --------------------------------------------------------------- header
st.title("B-READY 2025 -- Custom Index & Policy Impact Simulator")
st.caption(
    "The World Bank publishes no single overall economy score. This tool builds one, "
    "then simulates how indicator-level reforms move it. Underlying hierarchy is additive, "
    "so every simulated change is exact arithmetic."
)

worst = model.verification.max_abs_error.max()
st.success(f"Additivity verified across all 30 topic-pillars. Worst reconstruction error: {worst:.1e}")

lt = model.league_table(bands=bands, **WKW)
base_row = lt.loc[country]

c1, c2, c3, c4 = st.columns(4)
c1.metric("Composite score", f"{base_row.composite:.2f}")
c2.metric("Rating", base_row.rating)
c3.metric("Global rank", f"{int(base_row['rank'])} / {len(lt)}")
c4.metric("Topics", len(TOPIC_SHEETS))

tab_rank, tab_sim, tab_bench, tab_gap, tab_data = st.tabs(
    ["League table", "Policy simulator", "Benchmarking", "Gap analysis", "Data"])

# --------------------------------------------------------------- league
with tab_rank:
    st.subheader("Implied global ranking")
    st.dataframe(lt, use_container_width=True, height=460)
    st.bar_chart(model.topic_scores(pw).loc[country].sort_values())
    st.caption(f"{country} -- score by topic.")

# --------------------------------------------------------------- simulator
with tab_sim:
    st.subheader("What-if policy simulation")

    topic = st.selectbox("Topic", list(TOPIC_SHEETS),
                         index=list(TOPIC_SHEETS).index("Taxation"))
    cat = model.indicator_catalogue(topic)
    cat = cat.assign(current=[
        float(model.frames[topic].loc[country, c.key])
        for c in model._cols(topic, "indicator")
    ])
    cat["gap"] = (cat.frontier - cat.current).round(3)

    st.markdown("**Pick the indicators your reform would change**")
    picks = st.multiselect(
        "Indicators", cat.indicator.tolist(),
        default=cat.sort_values("gap", ascending=False).indicator.head(3).tolist(),
        format_func=lambda x: f"{x}  (gap {float(cat.loc[cat.indicator == x, 'gap'].iloc[0]):.2f})",
    )

    sc = Scenario("Custom reform", country)
    for ind in picks:
        row = cat[cat.indicator == ind].iloc[0]
        v = st.slider(ind, 0.0, float(row.frontier), float(row.frontier),
                      0.01, key=f"sl_{ind}",
                      help=f"Currently {row.current:.2f}. Frontier {row.frontier:.2f}.")
        sc.set(topic, ind, v)

    if len(sc) == 0:
        st.info("Select at least one indicator.")
    else:
        r = model.simulate(sc, bands=bands, **WKW)
        d1, d2, d3 = st.columns(3)
        d1.metric("Composite", f"{r['simulated_score']:.2f}", f"{r['delta']:+.2f}")
        d2.metric("Rating", r["simulated_rating"],
                  "unchanged" if r["simulated_rating"] == r["baseline_rating"]
                  else f"from {r['baseline_rating']}")
        d3.metric("Global rank", r["simulated_rank"],
                  f"{r['rank_change']:+d}" if r["rank_change"] else "unchanged")

        st.markdown("**Pillar effect**")
        st.dataframe(r["pillars"], use_container_width=True, hide_index=True)
        st.markdown("**Topic effect**")
        st.dataframe(r["topics"][r["topics"].delta.abs() > 1e-9],
                     use_container_width=True)
        st.caption(
            "Pillar 3 (Operational Efficiency) comes from firm surveys and administrative "
            "outcomes. Moving it requires delivery, not drafting -- treat any Pillar 3 "
            "change as an assumption, not a forecast."
        )

# --------------------------------------------------------------- benchmarking
with tab_bench:
    st.subheader("Comparative benchmarking")
    default_peers = [x for x in ["Georgia", "Armenia", "Azerbaijan", "Kazakhstan",
                                 "Estonia", "Korea, Rep."] if x in model.economies]
    chosen = st.multiselect("Economies", model.economies, default=default_peers)
    if chosen:
        st.dataframe(model.benchmark(chosen, bands=bands, **WKW),
                     use_container_width=True)
    st.markdown("**Immediate neighbours in the table**")
    st.dataframe(model.peers(country, 5, bands=bands, **WKW), use_container_width=True)

# --------------------------------------------------------------- gaps
with tab_gap:
    st.subheader("Distance to frontier")
    gtopic = st.selectbox("Topic (blank = all)", ["All"] + list(TOPIC_SHEETS))
    n = st.slider("Rows", 5, 60, 25)
    rep = model.frontier_gap_report(country, None if gtopic == "All" else gtopic, top=n)
    st.dataframe(rep, use_container_width=True, hide_index=True)
    st.caption(
        "Frontier = highest value observed among the 101 economies, so it is a level "
        "someone has actually reached. It is a lower bound on the theoretical maximum: "
        "point allocations inside some subcategories vary by economy context. Replace "
        "with Methodology Handbook maxima via set_ceilings() when precision matters."
    )

# --------------------------------------------------------------- data
with tab_data:
    st.subheader("Indicator catalogue")
    st.dataframe(model.indicator_catalogue(), use_container_width=True, hide_index=True)
    if model.answers_path:
        st.subheader("Underlying question responses")
        at = st.selectbox("Topic ", list(TOPIC_SHEETS), key="ans_topic")
        st.dataframe(model.answers(at, country), use_container_width=True, hide_index=True)
