"""
B-READY Custom Index & Policy Simulator -- Streamlit dashboard.

Run:
    pip install streamlit pandas numpy openpyxl
    streamlit run app.py
"""

import os

import altair as alt
import pandas as pd
import streamlit as st

from bready_simulator import (
    DEFAULT_BANDS, GROUPINGS, PILLAR_NAMES, TOPIC_SHEETS, BReadyModel, Scenario,
)

st.set_page_config(page_title="B-READY Simulator", layout="wide")

SCORES_DEFAULT = "01_B-READY-2025-PILLAR-TOPIC-SCORES.xlsx"
ANSWERS_DEFAULT = "02_B-READY-2025-EconomyAnswer.xlsx"
MAXIMA_DEFAULT = "bready_2025_indicator_maxima.csv"
REGIONS_DEFAULT = "regions.csv"

# chart colours (categorical slots 1-2, text and recessive ink)
C_COUNTRY, C_GROUP, C_MUTED, C_RULE = "#2a78d6", "#eb6834", "#c9c8c2", "#52514e"


@st.cache_resource(show_spinner="Loading B-READY 2025 data...")
def load_model(scores: str, answers: str | None, maxima: str | None):
    m = BReadyModel(scores, answers if answers and os.path.exists(answers) else None,
                    maxima_path=maxima if maxima and os.path.exists(maxima) else None)
    if os.path.exists(REGIONS_DEFAULT):
        m.load_regions(REGIONS_DEFAULT)
    return m


# --------------------------------------------------------------- sidebar
st.sidebar.title("B-READY Simulator")

scores_path = st.sidebar.text_input("Scores workbook", SCORES_DEFAULT)
answers_path = st.sidebar.text_input("Answers workbook", ANSWERS_DEFAULT)
maxima_path = st.sidebar.text_input("Per-economy maxima (CSV)", MAXIMA_DEFAULT)

if not os.path.exists(scores_path):
    st.error(f"Scores workbook not found at `{scores_path}`. Set the path in the sidebar.")
    st.stop()

model = load_model(scores_path, answers_path, maxima_path)
if not model.maxima_path:
    st.sidebar.warning("Maxima CSV not found: ceilings fall back to the cross-country frontier.")

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

tab_rank, tab_sim, tab_bench, tab_reg, tab_gap, tab_data = st.tabs(
    ["League table", "Policy simulator", "Benchmarking", "Regions", "Gap analysis", "Data"])

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
    cat = model.indicator_catalogue(topic, country)
    n_na = int(cat.not_applicable.sum())
    cat = cat[~cat.not_applicable]                      # N/A for this economy: not a reform option
    cat["gap"] = (cat.ceiling - cat.current).round(3)
    if n_na:
        st.caption(f"{n_na} indicator(s) in {topic} do not apply to {country} and are hidden.")

    st.markdown("**Pick the indicators your reform would change**")
    # Suggest the biggest gaps among law-based indicators (Pillars 1-2). Pillar 3 is outcomes
    # (survey times, costs, tax rates) and stays available, but is not suggested by default.
    suggest = (cat[(cat.pillar != 3) & (cat.gap > 1e-9)]
               .sort_values("gap", ascending=False).indicator.head(3).tolist())
    picks = st.multiselect(
        "Indicators", cat.indicator.tolist(), default=suggest,
        key=f"picks_{country}_{topic}",
        format_func=lambda x: (f"{x}  (P{int(cat.loc[cat.indicator == x, 'pillar'].iloc[0])}, "
                               f"gap {float(cat.loc[cat.indicator == x, 'gap'].iloc[0]):.2f})"),
    )

    sc = Scenario("Custom reform", country)
    for ind in picks:
        row = cat[cat.indicator == ind].iloc[0]
        p3 = int(row.pillar) == 3
        # slider starts at TODAY's value: nothing changes until the user moves it
        v = st.slider(f"{ind}  (today {row.current:.2f}, max {row.ceiling:.2f})"
                      f"{'  [Pillar 3: assumption]' if p3 else ''}",
                      0.0, float(row.ceiling), float(row.current), 0.01,
                      key=f"sl_{country}_{topic}_{ind}",
                      help=f"Today: {row.current:.2f} points. Maximum for {country}: "
                           f"{row.ceiling:.2f}. Drag right to simulate a reform.")
        if abs(v - row.current) > 1e-9:
            sc.set(topic, ind, v)

    if not picks:
        st.info("Select at least one indicator.")
    elif len(sc) == 0:
        st.info("Sliders start at today's values. Move one to see the effect of a reform.")
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

# --------------------------------------------------------------- regions
with tab_reg:
    st.subheader("Regional comparison")
    groupings = [g for g in GROUPINGS if g == "Neighbourhoods" or hasattr(model, "regions")]
    grouping = st.radio("Group economies by", groupings, horizontal=True)
    gt = model.group_table(grouping, **WKW)
    comp = model.composite(**WKW)
    own = model.groups_of(country, grouping)

    # --- 1. group averages, with the target economy as a reference line
    bars = gt.reset_index()[["group", "n", "composite"]]
    bars["is_own"] = bars.group.isin(own)
    bars["label"] = bars.apply(lambda r: f"{r.group}  (n={r.n})", axis=1)
    base = alt.Chart(bars).encode(
        y=alt.Y("label:N", sort="-x", title=None, axis=alt.Axis(labelLimit=260, labelOverlap=False)),
        x=alt.X("composite:Q", title="Average composite score (0-100)",
                scale=alt.Scale(domain=[0, 100])),
        tooltip=[alt.Tooltip("group:N", title="Group"), alt.Tooltip("n:Q", title="Economies"),
                 alt.Tooltip("composite:Q", title="Average", format=".2f")],
    )
    chart = base.mark_bar(cornerRadiusEnd=4, height=18).encode(
        color=alt.condition("datum.is_own", alt.value(C_COUNTRY), alt.value(C_MUTED)))
    text = base.mark_text(align="left", dx=4, color=C_RULE).encode(text=alt.Text("composite:Q", format=".1f"))
    rule = alt.Chart(pd.DataFrame({"x": [float(comp[country])], "t": [f"{country} {comp[country]:.1f}"]}))
    rule = (rule.mark_rule(color=C_RULE, strokeDash=[4, 3], strokeWidth=2).encode(x="x:Q")
            + rule.mark_text(align="left", dx=4, dy=-6, color=C_RULE, fontWeight="bold")
            .encode(x="x:Q", y=alt.value(0), text="t:N"))
    st.altair_chart((chart + text + rule).properties(height=max(180, 44 * len(bars))),
                    use_container_width=True)
    st.caption(f"Blue = group(s) {country} belongs to. Dashed line = {country}'s own composite. "
               "Averages are simple means of the member economies.")

    # --- 2. topic profile: target economy vs one group's average
    if len(gt):
        choices = list(gt.index)
        default = choices.index(own[0]) if own else 0
        g = st.selectbox("Compare against", choices, index=default)
        topics = list(TOPIC_SHEETS)
        ts = model.topic_scores(pw)
        prof = pd.DataFrame({"topic": topics,
                             country: ts.loc[country, topics].values,
                             f"{g} average": gt.loc[g, topics].values.astype(float)})
        prof["gap"] = prof[country] - prof[f"{g} average"]
        long = prof.melt(id_vars=["topic", "gap"], var_name="series", value_name="score")
        order = prof.sort_values("gap").topic.tolist()       # weakest relative topics first
        y = alt.Y("topic:N", sort=order, title=None, axis=alt.Axis(labelLimit=200, labelOverlap=False))
        link = alt.Chart(prof).mark_rule(color=C_MUTED, strokeWidth=2).encode(
            y=y, x=alt.X(f"{country}:Q"), x2=f"{g} average:Q")
        dots = alt.Chart(long).mark_circle(size=110, opacity=1, stroke="white", strokeWidth=2).encode(
            y=y, x=alt.X("score:Q", title="Topic score (0-100)", scale=alt.Scale(zero=False)),
            color=alt.Color("series:N", title=None,
                            scale=alt.Scale(domain=[country, f"{g} average"], range=[C_COUNTRY, C_GROUP]),
                            legend=alt.Legend(orient="top")),
            tooltip=[alt.Tooltip("topic:N", title="Topic"), alt.Tooltip("series:N", title="Series"),
                     alt.Tooltip("score:Q", format=".1f", title="Score"),
                     alt.Tooltip("gap:Q", format="+.1f", title=f"{country} vs average")],
        )
        st.altair_chart((link + dots).properties(height=380), use_container_width=True)
        members = model.groups(grouping)[g]
        st.caption(f"Topics sorted from where {country} trails the {g} average most (top) "
                   f"to where it leads most (bottom)."
                   + (f" The average includes {country} itself." if country in members else ""))

        c1, c2, c3 = st.columns(3)
        within = comp[members].sort_values(ascending=False)
        c1.metric(f"{country} composite", f"{comp[country]:.2f}")
        c2.metric(f"{g} average", f"{gt.loc[g, 'composite']:.2f}",
                  f"{comp[country] - gt.loc[g, 'composite']:+.2f} gap" if country in members else None)
        if country in members:
            c3.metric(f"Rank within {g}", f"{list(within.index).index(country) + 1} / {len(members)}")
        st.markdown(f"**Members of {g}**")
        st.dataframe(pd.DataFrame({"composite": within.round(2),
                                   "global rank": comp.rank(ascending=False, method="min")[within.index].astype(int)}),
                     use_container_width=True)

    st.markdown("**All groups: average score by topic**")
    st.dataframe(gt.round(1), use_container_width=True)
    note = ("Neighbourhood groups are defined in this tool and only include economies covered by "
            "B-READY 2025 (e.g. Middle East here = Bahrain, Israel, Jordan, West Bank and Gaza). ")
    if grouping != "Neighbourhoods":
        note += ("World Bank regions and income groups follow WDI country metadata "
                 "(the World Bank places Malta and Pakistan in Middle East & North Africa). ")
    st.caption(note + "Groups with few economies give noisy averages: check n.")

# --------------------------------------------------------------- gaps
with tab_gap:
    st.subheader("Distance to maximum")
    gtopic = st.selectbox("Topic (blank = all)", ["All"] + list(TOPIC_SHEETS))
    n = st.slider("Rows", 5, 60, 25)
    rep = model.frontier_gap_report(country, None if gtopic == "All" else gtopic, top=n)
    st.dataframe(rep, use_container_width=True, hide_index=True)
    st.caption(
        "Ceiling = this economy's own maximum points per indicator, taken from its 2025 "
        "B-READY profile (maxima differ by economy: missing survey data, no VAT, landlocked/"
        "island trade rules). Frontier = best value seen across the 101 economies, shown "
        "for reference only. Indicators that do not apply to this economy are excluded."
    )

# --------------------------------------------------------------- data
with tab_data:
    st.subheader("Indicator catalogue")
    st.dataframe(model.indicator_catalogue(), use_container_width=True, hide_index=True)
    if model.answers_path:
        st.subheader("Underlying question responses")
        at = st.selectbox("Topic ", list(TOPIC_SHEETS), key="ans_topic")
        ans = model.answers(at, country).astype({"Economy Response": str})   # mixed text/numbers
        st.dataframe(ans, use_container_width=True, hide_index=True)
