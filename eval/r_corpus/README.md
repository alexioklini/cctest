# R eval corpus

Real-world R scripts used by `eval/r_mistral_eval.py` (and as a fixture for the
code-mode R-analysis feature, `engine/tools/r_analysis.py`). Provided by the user;
checked in so the eval is reproducible without a scratchpad path.

Domain: bank **liquidity / credit-risk** modelling (Base-R + one tidyverse script).

| File | What it is |
|------|------------|
| `ECL_122025.R` | IFRS-9 Expected-Credit-Loss main run (sources the helper) |
| `ECL_Hilfsfunktion.R` / `ECL_Hilfsfunktion_2.R` | ECL helper functions — **two versions of the same 6 functions** (duplicate/version-drift example) |
| `PIT_122025.R` | Point-in-Time PD computation |
| `TTC_122025.R` | Through-the-Cycle PD (`get_rho` Vasicek, `get_cumpd`) |
| `TL_122025.R` | Through-the-Lifetime computation |
| `Ablaufmodellierung.R` | Liquidity run-off modelling — GBM + Monte-Carlo (`Ablaufmodellierung_GBM/_BS`, `rtbis`, `probFirstHittingTime2`, `StochastikTool`) |
| `LAB_Berechnung.R` | Orchestrator — `source("Ablaufmodellierung.R")` then runs it |
| `Bodensatz_Tests_Normalverteilung.R` | tidyverse + ggplot2 normality tests (KS / Jarque-Bera / Shapiro) |

Characteristics that make this a good test set: Base-R **and** tidyverse styles,
heavy **global-state coupling** (`PD_cube`, `pdtime`, `interpol` used inside
functions but not passed), a `source()` dependency graph, duplicate helper
functions, and hand-rolled quant models (no `lm`/`glm`).

These are data/code samples only — not part of the running application.
