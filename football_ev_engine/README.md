# Football +EV Engine

A command-line engine that prices football matches with a Dixon-Coles model,
strips the margin from sharp (Pinnacle) odds, looks for positive expected
value (+EV) at soft bookmakers, and sizes stakes with quarter-Kelly.

It covers Serie A, the Premier League, La Liga, the Bundesliga and Ligue 1.
Historical data comes from football-data.co.uk and live odds come from The Odds API.

> **Read this first.** Closing lines at sharp books are among the most
> efficient prices in sport. Most days, the honest result of this engine is
> "no bets pass the filters". Run the backtest and check the CLV line before
> you stake real money. None of this is financial advice, and betting may be
> restricted where you live.

## Setup

```bash
cd football_ev_engine
python -m venv .venv && source .venv/bin/activate   # Python 3.11+
pip install -r requirements.txt
cp .env.example .env            # then put your key in ODDS_API_KEY
```

A free key from [the-odds-api.com](https://the-odds-api.com) gives you 500 credits a month.
Each `fetch-odds` call costs 2 credits per league (2 markets × 1 region), and listing the sports is free.

## Commands

```bash
python main.py init-db                         # create the DuckDB schema
python main.py fetch-historical                # current season + 4 previous, all 5 leagues
python main.py train                           # fit Dixon-Coles + ML benchmark per league
python main.py fetch-odds                      # upcoming odds, team names mapped to history
python main.py predict --league serie-a        # +EV table with stakes
python main.py backtest --league serie-a --seasons 2023-2024,2024-2025
python main.py aliases --league serie-a        # review name mappings
python main.py aliases --league serie-a --set "Inter Milan=Inter"   # pin one by hand
```

Useful options:

| Command | Option | Purpose |
|---|---|---|
| `fetch-historical` | `--league premier-league,la-liga` `--seasons 2022-2023,2023-2024` `--n-seasons 3` | Limit what is downloaded |
| `train` | `--ml gbm` / `--ml none` | Choose the benchmark classifier |
| `predict` | `--bankroll 500` `--min-ev 0.05` `--model-weight 0.5` `--show-skipped` | Override filters for one run |
| `backtest` | `--refit-days 14` `--no-totals` `--model-weight 0.2` `--ml none` | Simulation settings |

The league names are `serie-a`, `premier-league` (or `epl`), `la-liga`, `bundesliga` and `ligue-1`.
Every threshold can also be set in `.env`. See `.env.example` for the list.

## How it works

### 1. Data

| Table | Contents |
|---|---|
| `matches` | Results, shots, Bet365 and Pinnacle 1X2 odds (pre-match and closing), market average, and O/U 2.5 odds |
| `fixtures` / `odds` | The latest Odds API snapshot. Team names are mapped to the history's naming |
| `team_aliases` | Cached name mappings, with the method (exact/seed/fuzzy/manual) and a verified flag |
| `model_params` | Fitted Dixon-Coles parameters (JSON) per league |
| `api_usage` | Odds API quota headers from every call |
| `value_bets` | Every bet `predict` recommended, for later review |

The CSV parser handles the format changes between seasons, including older column names,
`dd/mm/yy` versus `dd/mm/yyyy` dates, trailing empty columns and latin-1 encoding.
Re-running an ingest updates rows in place and never duplicates them.

### 2. Team names

API names are resolved to the historical names in this order:

1. A cached or hand-pinned alias.
2. An exact match after normalisation (accents and tokens like "FC"/"AC"/"Calcio" removed).
3. A seed list of known mismatches (`src/data/team_aliases.json`, e.g. *Inter Milan → Inter*, *Nottingham Forest → Nott'm Forest*).
4. `rapidfuzz.process.extractOne` with a score cutoff of 80.

Matching is one-to-one within each fetch, so a fuzzy guess can't take a team that another name already matched exactly.
Fuzzy matches are saved as *unverified*, and you can review them with `aliases`.
The table is exported to `data/team_aliases.json` after every fetch, so it survives a database rebuild.

### 3. Dixon-Coles (`src/models/dixon_coles.py`)

λ = exp(α_home + β_away + γ) and μ = exp(α_away + β_home), with the Dixon-Coles
τ correction on 0-0, 1-0, 0-1 and 1-1. The model maximises the time-weighted
log-likelihood with weight e^(−ξ·days). The default ξ is 0.005/day, a
half-life of about 139 days. It uses L-BFGS-B with an **analytic gradient**,
and the constraint Σα = 0 keeps the model identifiable.

Cold starts come from an independent-Poisson GLM (statsmodels). Refits in the
backtest are warm-started from the previous fit. One fit takes well under a
second. The test suite checks that the model recovers the known parameters of simulated leagues.

### 4. De-vigging, value and stakes

* **De-vig** (`engine/devig.py`): P_fair = (1/O) / (1 + M). A `power` method is also available (`DEVIG_METHOD=power`); it puts more of the margin on longshots.
* **Probability** (`engine/value.py`): P_model = w · P_DixonColes + (1 − w) · P_Pinnacle, with `MODEL_WEIGHT` w = 0.35 by default. If Pinnacle has no price, the de-vigged consensus of the soft books is used instead (marked `*` in the table).
* **EV** = P_model × O_soft − 1. Only the best soft price per selection is kept.
* **Filters**: EV ≥ 3%, P_model ≥ 0.35 and 1.40 ≤ odds ≤ 4.50.
* **Stake** (`engine/staking.py`): f = 0.25 × (p·b − q)/b, capped at 2.5% of the bankroll.

**Why the blend?** The spec's formula uses P_model. If P_model were the raw
Dixon-Coles number, every gap between the model and a sharp line would count as
"value". Most of those gaps are model error, not market error. Moving the
estimate toward the de-vigged Pinnacle price keeps only the gaps that are large
enough to matter. `--model-weight 1` gives the pure-model engine, and
`--model-weight 0` gives a pure "soft book vs sharp line" scanner. Pick *w*
from the backtest's Brier table rather than by feel.

Markets priced: 1X2 (`h2h`) and totals on half-goal lines. Integer and
quarter lines are skipped because they need push handling.

### 5. Backtest (`backtest/backtester.py`)

The backtest is walk-forward. Every `--refit-days` (default 7), Dixon-Coles is
refit only on matches **before** that block and then prices the block. A test
confirms that rewriting later results never changes an earlier bet.

It bets Bet365 pre-match prices against Pinnacle pre-match prices, using the
same `evaluate_market` rules as the live engine. Stakes are sized on each match
day's opening bankroll.

| Metric | Meaning |
|---|---|
| Yield | profit / total staked |
| ROI | bankroll growth over the period |
| Max drawdown | largest peak-to-trough fall of the bankroll |
| Avg CLV | EV of each bet price against Pinnacle's de-vigged *closing* line. Consistently positive CLV is the best evidence of a real edge. Yield over a few hundred bets is mostly noise |
| Brier / log loss | 1X2 accuracy of Dixon-Coles, de-vigged Pinnacle, the blend and the ML benchmark, all scored on the same matches |

The ML benchmark (`models/ml_classifier.py`) uses rolling pre-match form
(goals, shots, shots on target, points) with multinomial logistic regression,
or `--ml gbm` for scikit-learn's gradient boosting. XGBoost isn't needed. The
benchmark is refit before each test season.

## Tests

```bash
cd football_ev_engine
pytest -q
```

There are 90 tests. They run offline: HTTP is mocked with `respx`, and a
synthetic league generator (`tests/synthetic.py`) produces files in the
football-data and Odds API formats. `tests/test_cli.py` runs every command from
start to finish.

## Known limits

* **Bookmaker keys.** The default soft books are `bet365, unibet_eu, unibet, sport888, marathonbet`. The Odds API may not offer every one of them (Bet365 in particular) in the `eu` region. Books it doesn't return are skipped. Set `SOFT_BOOKMAKERS='["unibet_eu","sport888",...]'` to match what your region returns.
* **Promoted teams** can't be priced by Dixon-Coles until they have played in the league. For those fixtures the engine uses the Pinnacle price alone, or skips them if there is none.
* **Correlated bets.** Several bets on one match (e.g. home win and under 2.5) are each staked on their own. The 2.5% cap limits the exposure, but the stakes are not jointly optimised.
* **Kick-off timing.** The historical "pre-match" odds were collected a day or two before kick-off (football-data's convention), so live execution at other times will differ.
