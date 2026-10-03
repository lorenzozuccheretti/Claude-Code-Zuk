# Football +EV Engine

A command-line engine that prices football matches with a Dixon-Coles model,
strips the margin from sharp (Pinnacle) odds, looks for positive expected
value (+EV) at soft bookmakers, and sizes stakes with quarter-Kelly.

It covers Serie A, the Premier League, La Liga, the Bundesliga, Ligue 1 and the UEFA Nations League.
Club history comes from football-data.co.uk, national-team history from
[martj42/international_results](https://github.com/martj42/international_results), and live odds from The Odds API.

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

### National teams (UEFA Nations League)

National-team results come from one free CSV: every men's international since 1872, with a neutral-venue flag. The model keeps 4 years of these, with a slower decay (half-life about a year), because a national side plays only about 10 matches a year. Home advantage is switched off at neutral venues, both in fitting and in prediction. Non-FIFA sides (CONIFA and similar) are left out.

On 1,009 internationals held out of the fit (July 2025 onwards), the model's Brier score was 0.491, against 0.635 for "always the average". **No archive of past odds exists for internationals, so the betting strategy cannot be backtested there.** The only market test is the bot's own recorded CLV. Two more limits: the dataset is maintained by hand and can lag real results by days or weeks, so recent form may be missing and settlement waits for the update; and national sides rotate squads far more than clubs do.

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

## Daily Telegram agent

`python main.py agent run` does the whole day's job in one pass and posts at
most two picks to a Telegram chat:

1. Refreshes the current season from football-data (the full history on the first run) and settles earlier picks.
   Results the slow sources don't have yet (football-data updates a couple of times a week; the national-team file can lag by weeks) are filled from The Odds API `/scores` as *provisional* rows, so the model trains on last night's games. It only calls `/scores` for a league whose round is over, or whose oldest missing result is about to leave the API's 3-day window: about 2 credits per league per round. Provisional rows are dropped once the official source publishes the match.
2. Refits Dixon-Coles for all five leagues.
3. Fetches fresh odds. That costs 10 credits a run for the five leagues, plus 2 while the Nations League is on: about 300–360 a month on the free tier's 500.
4. Keeps outcomes priced **1.75–2.25** with **EV ≥ +3.5%** and kick-off 1–36 hours away. It ranks them by **EV × P_model** and sends the top 1–2 (0 if nothing qualifies).
5. Never sends a fixture twice. Only one pick per match is allowed, because a home win and an under on the same game are correlated. The cap is 2 per local calendar day, even if the job runs more than once.

Each message carries the match, local kick-off time, selection, best soft price with its bookmaker,
engine probability and fair odds, EV, a quarter-Kelly stake, and a two-sentence rationale. The last message of the day also shows the running track record.

**The rationale.** It is built from facts the engine computed: last-five form,
the model's expected goals, defensive rank, and the shots-on-target trend.
football-data has no xG, so the model's expected goals and shots on target
stand in for it. With `ANTHROPIC_API_KEY` set, Claude (`claude-opus-5`, low
effort) writes the two sentences under a "use only these facts" instruction,
with server-side refusal fallback enabled. Without a key, or if the call
fails, a deterministic template writes them.

**The track record.** Each pick is graded the day after the match from The Odds API's results endpoint (2 credits per competition with a pending pick). Once football-data publishes Pinnacle's closing prices, the pick also gets its **CLV against the de-vigged closing line**. After 50–100 picks, the CLV tells you whether the edge is real, long before profit does.

**Reporting.** Every Monday (`REPORT_WEEKDAY`, 0 = Monday, -1 = off), the agent posts a summary. It covers the week and all-time results, P/L at flat 1-unit stakes and at the suggested Kelly stakes, average odds, EV and CLV, a breakdown by competition, market and month, and the last 10 picks with ✅/❌/⏳. A verdict line is based on CLV. The full history comes attached as `picks_history.csv`, which opens in Excel or Google Sheets. On demand: `python main.py agent report [--send] [--days 30] [--csv file.csv]`, or **Run workflow → mode: report** on GitHub. `agent history` lists each pick.

**Once a day, even with retries.** Every run records itself. A second run on the same local day (a backup cron, or a re-run) stops before spending any credits, unless it is started with `--force`. The "no pick today" notice and the weekly report are also sent at most once. A failed Telegram send leaves the day open, so the next backup retries it.

### Setup

1. Create a bot: message [@BotFather](https://t.me/BotFather), send `/newbot`, and copy the token.
2. Get your chat id: send any message to the bot, then open `https://api.telegram.org/bot<TOKEN>/getUpdates` and read `message.chat.id`. For a channel, add the bot as an admin and use `@channelname` or the channel's `-100…` id.
3. Put `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` and `ODDS_API_KEY` in `.env` (see `.env.example`).
4. Check the connection with `python main.py agent test-telegram`, and preview a run with `python main.py agent run --dry-run`.

### Running it every day

| Where | How |
|---|---|
| Local machine or server | `python main.py agent schedule` runs every day at `RUN_TIME` in `TIMEZONE` (APScheduler). Add `--run-now` to also run once immediately |
| Docker | `docker compose up -d --build` runs the scheduler, with the database in `./data` |
| GitHub Actions | `.github/workflows/football-agent.yml` runs daily at 07:41 UTC, with backups at 09:23 and 11:07 that do nothing once the day is done (GitHub often starts top-of-the-hour schedules late, or skips them). It can also be started by hand in `run`, `dry-run` or `report` mode. Add the secrets it lists. The database is carried between runs in the Actions cache, and GitHub evicts that cache after 7 days without a run |
| cron | `0 10 * * * cd /path/football_ev_engine && python main.py agent run` |

### What the backtest says about these rules

These are the agent's exact rules, run walk-forward over 2024-25 and 2025-26 on
all five leagues. Stakes are 1 unit on Bet365 prices, with the default model
weight of 0.35:

| Picks | Hit rate | Yield | Avg CLV |
|---|---|---|---|
| 277 | 46.2% | −8.1% | −4.4% |

At every model weight tested (0.2–1.0), CLV comes out around −4.5%. That is
about the soft book's margin, so the chosen prices are no better than random
ones. Live, the agent takes the best price across ~16 bookmakers rather than
Bet365 alone, which helps somewhat. Still, treat the first weeks as a paper
trial, and let the recorded CLV decide.

## Tests

```bash
cd football_ev_engine
pytest -q
```

There are 136 tests. They run offline: HTTP is mocked with `respx`, and a
synthetic league generator (`tests/synthetic.py`) produces files in the
football-data and Odds API formats. `tests/test_cli.py` runs every command from
start to finish.

## Known limits

* **Bookmaker keys.** The default soft books are the ~16 mainstream European books The Odds API returns for `regions=eu` (William Hill, Unibet, Betclic, Winamax, Tipico, Codere and others; see `soft_bookmakers` in `src/config.py`). Bet365 is not offered there. Exchanges and offshore books are left out. To bet only where you hold an account (for example ADM-licensed books in Italy), set `SOFT_BOOKMAKERS='["codere_it","williamhill",...]'`.
* **Promoted teams** can't be priced by Dixon-Coles until they have played in the league. For those fixtures the engine uses the Pinnacle price alone, or skips them if there is none.
* **Correlated bets.** Several bets on one match (e.g. home win and under 2.5) are each staked on their own. The 2.5% cap limits the exposure, but the stakes are not jointly optimised.
* **Kick-off timing.** The historical "pre-match" odds were collected a day or two before kick-off (football-data's convention), so live execution at other times will differ.
