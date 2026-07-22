# B-READY 2025 — Custom Index & Policy Impact Simulator

Builds a single composite rating from World Bank Business Ready 2025 data
(which publishes no overall economy score) and simulates how indicator-level
reforms move that rating and an economy's implied global rank.

## Install

```bash
pip install -r requirements.txt
```

Put the two World Bank workbooks in the same folder:

- `01_B-READY-2025-PILLAR-TOPIC-SCORES.xlsx`
- `02_B-READY-2025-EconomyAnswer.xlsx`

## Run

```bash
python bready_simulator.py 01_B-READY-2025-PILLAR-TOPIC-SCORES.xlsx 02_B-READY-2025-EconomyAnswer.xlsx
streamlit run app.py
```

## The aggregation pipeline

The published hierarchy is strictly additive below the pillar level:

```
indicator points --sum--> subcategory --sum--> category --sum--> pillar (/100)
pillar --weighted mean--> topic
topic  --weighted mean--> composite (0-100)
```

Verified on load: reconstructing each of the 30 topic-pillars from its
indicator columns reproduces the published pillar score to ~1e-14. Because
the tree is additive, a reform simulation is exact arithmetic — not a
regression, not a forecast.

## Ceilings, and why this matters

The dataset does not publish maximum attainable points per indicator. Two
references are provided:

- **frontier** (default) — the highest value observed across the 101
  economies. Empirically grounded: some economy has actually achieved it.
- **handbook** — true maxima from the Methodology Handbook, supplied via
  `model.set_ceilings({(topic, indicator): max_points})`.

Frontier values are applied per indicator and never summed to claim an
attainable pillar total, because point allocations inside some subcategories
vary by economy context.

## Interpreting results

Pillar 1 (Regulatory Framework) is scored from the legal framework, so a
simulated change there is a recalculation. Pillar 2 (Public Services) needs
delivery, not just drafting. Pillar 3 (Operational Efficiency) comes from
firm surveys and administrative outcomes — treat any simulated change there
as an explicit assumption, never a prediction.

Source: B-READY 2025, World Bank Group. Data CC BY 4.0. This tool and its
composite rating are independent and not endorsed by the World Bank.
