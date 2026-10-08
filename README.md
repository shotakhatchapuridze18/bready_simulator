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
- `bready_2025_indicator_maxima.csv` (per-economy maxima parsed from the 101 profile PDFs)
- `bready_data_loader.py` (joins the CSV to the scores workbook)

## Run

```bash
python bready_simulator.py 01_B-READY-2025-PILLAR-TOPIC-SCORES.xlsx 02_B-READY-2025-EconomyAnswer.xlsx bready_2025_indicator_maxima.csv
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

Maximum points differ by economy (missing Enterprise Survey data, no VAT,
landlocked/island trade rules). The workbook does not publish them; the 101
economy profile PDFs do. With the maxima CSV loaded:

- **ceiling** — the economy's own maximum per indicator (`model.ceiling()`).
  If the economy already earns more than the printed maximum (8 profile
  cells), the earned value is used.
- **not applicable** — indicators with a maximum of 0 for that economy are
  hidden from the simulator and gap report (`model.is_na()`).
- **frontier** — best value across the 101 economies, kept for reference only.
  Without the CSV the model falls back to it.

## Interpreting results

Pillar 1 (Regulatory Framework) is scored from the legal framework, so a
simulated change there is a recalculation. Pillar 2 (Public Services) needs
delivery, not just drafting. Pillar 3 (Operational Efficiency) comes from
firm surveys and administrative outcomes — treat any simulated change there
as an explicit assumption, never a prediction.

Source: B-READY 2025, World Bank Group. Data CC BY 4.0. This tool and its
composite rating are independent and not endorsed by the World Bank.
