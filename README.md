# ig_roadmap
# `ftd` package: first-time deposit (FTD) and post-deposit behaviour analysis

The package measures how registered players become paying players (registration → first deposit, FTD) and what they do right after the first deposit (second deposit, STD, and early value). Data is read from two parquet files (players and transactions) and results are printed to the console.

Contents:

1. [Quick start](#1-quick-start)
2. [Package layout and data flow](#2-package-layout-and-data-flow)
3. [Key concepts](#3-key-concepts)
4. [Input data](#4-input-data)
5. [Configuration: `ftd_vars.py`](#5-configuration-ftd_varspy)
6. [Core: `ftd_core.py`](#6-core-ftd_corepy)
7. [Stage 1: `ftd_inventory.py`](#7-stage-1-ftd_inventorypy)
8. [Stage 2: `ftd_stat.py`](#8-stage-2-ftd_statpy)
9. [Stage 3: `ftd_std.py`](#9-stage-3-ftd_stdpy)
10. [Entry point: `__main__.py`](#10-entry-point-__main__py)
11. [Dataframe column reference](#11-dataframe-column-reference)
12. [Common errors](#12-common-errors)
13. [Extending the package](#13-extending-the-package)

---

## 1. Quick start

### Requirements

- Python 3.10+ (annotations use the `dict | None` syntax).
- `pandas`, `numpy`, `pyarrow`. The code was written for pandas 2.x.
- The project module `ig_roadmap.project_core` with two functions: `print_df()` (configures pandas console output) and `title(text, width=...)` (prints a section header). This module is not part of the `ftd` package and must exist as `ig_roadmap/project_core.py`.
- Two parquet files with data (see [section 4](#4-input-data)).

### File layout

```
ig_roadmap/
├── project_core.py          # external, not part of the ftd package
└── ftd/
    ├── __init__.py          # may be empty
    ├── __main__.py          # entry point
    ├── ftd_vars.py          # constants and settings
    ├── ftd_core.py          # data loading and FTD construction
    ├── ftd_inventory.py     # stage 1
    ├── ftd_stat.py          # stage 2
    └── ftd_std.py           # stage 3
```

### Running from the command line

Run the commands from the folder that contains the `ig_roadmap` directory (for example `D:/code`) using `-m`. Running `python ftd_inventory.py` directly from the `ftd` folder will not work: Python will not find the `ig_roadmap` package (`ModuleNotFoundError`).

```
python -m ig_roadmap.ftd                 # all three stages
python -m ig_roadmap.ftd inventory       # stage 1 only
python -m ig_roadmap.ftd stat            # stage 2 only
python -m ig_roadmap.ftd std             # stage 3 only
python -m ig_roadmap.ftd stat std        # several stages
```

Stages always run in the order `inventory` → `stat` → `std`, regardless of the order on the command line. Each module can also be run on its own: `python -m ig_roadmap.ftd.ftd_stat`.

If `inventory` runs, the data it builds is reused by the following stages. Otherwise each stage calls `core.run_pipeline()` itself and reads the data again.

### Using it from Python

```python
import ig_roadmap.ftd.ftd_core as core
import ig_roadmap.ftd.ftd_stat as stat
import ig_roadmap.ftd.ftd_std as std

res = core.run_pipeline()
ftd, deps, obs_end = res["ftd"], res["deps"], res["obs_end"]

# registration -> FTD conversion by channel
conv_by_chnl = stat.conversion_table(ftd, by="chnl")

# second deposit
ftd_std = std.build_std(deps, ftd, obs_end)
std_total = std.std_conversion(ftd_std)

# early value
ev = std.early_value(deps, ftd, obs_end)
ev_by_chnl = std.early_value_summary(ev, by="chnl")
```

### Environment variable

`IG_CURR_DATA_DIR` overrides the data folder. By default it is the `curr_data` folder inside `ig_roadmap`, next to the `ftd` package.

### Trial run on synthetic data

The script `make_synthetic_data.py` (in the repository root, next to the `ig_roadmap` folder) creates the two parquet files in the expected schema. No real data is involved.

```
python make_synthetic_data.py --out ig_roadmap/curr_data
python -m ig_roadmap.ftd
```

Options: `--players` (default 20,000), `--days` (registration period, 120), `--end` (observation end, `2026-10-31 23:59:59`), `--seed` (42), `--out` (output folder; defaults to `$IG_CURR_DATA_DIR` or `./curr_data`). The data has channel-dependent conversion, repeat deposits, failed deposits and withdrawals (the package must filter these out) and a cut-off at the observation end, which is exactly the right censoring the package handles.

---

## 2. Package layout and data flow

```
 ftd_vars.py  (constants, computes nothing)
      │
      ▼
 ftd_core.py  (load_tables → run_pipeline → ftd, deps, obs_end)
      │
      ├────────────► ftd_inventory.py   stage 1: data overview, FTD build, checks
      │
      ├────────────► ftd_stat.py        stage 2: FTD statistics (conversion, cohorts)
      │                    ▲
      │                    │ used by ftd_std: conversion_table, resolve_by, collapse_rare
      └────────────► ftd_std.py         stage 3: behaviour after the first deposit
```

File roles:

| File | Role | Side effects |
|---|---|---|
| `ftd_vars.py` | Constants only: paths, columns, business rules, analysis parameters | None |
| `ftd_core.py` | Shared logic: reading parquet, building the FTD table, checks | Only `show_schema` and `validate_ftd` print |
| `ftd_inventory.py` | Report on the source data and FTD | Prints, reads files |
| `ftd_stat.py` | Statistics functions and a report built from them | Prints only in `main()` |
| `ftd_std.py` | STD and early value functions and a report | Prints only in `main()` |
| `__main__.py` | Selects and runs stages | Prints via the stages |

Nothing runs at import time: all work happens inside functions.

---

## 3. Key concepts

**FTD (First-Time Deposit)**: a player's first successful deposit. "First" means the earliest `tzx_date`; if timestamps are equal, the transaction with the smaller `tx_id` wins.

**STD (Second-Time Deposit)**: a player's second successful deposit, determined with the same ordering.

**Successful deposit**: a transaction with `type == "deposit"` and `status == "success"` (after renaming, `tzx_status == "success"`).

**Calendar days vs fractional days.** The package uses both:
- `ftd_lag_days` is fractional days between registration and FTD (timestamp difference divided by 86400).
- `ftd_lag_cal_days` is the difference of calendar dates (0 means "on the same calendar day"). All windows D0, D1, D7 are measured in calendar days.

**Window Dn**: a player "converted within window Dn" if the target event happened no later than n calendar days after the start event. D0 is the same calendar day, D7 is within a week (day 0 included).

**Right censoring and maturity.** The data ends at `obs_end`. Recently registered players have not had time to deposit, and including them in the denominator would understate conversion. Therefore:
- A player is "mature" for window Dn if `age_cal_days > n`, meaning the n-th calendar day after registration was fully observed. The day of `obs_end` itself counts as incomplete.
- Conversion for window Dn is computed on mature players only. Different windows therefore have different denominators: D30 has fewer players than D0. The denominator size is always shown in the `eligible` or `n_D<ref>` columns.
- STD works the same way, but age is measured from the FTD (`ftd_age_cal_days`).

**Wilson confidence interval.** Every rate gets a 95% Wilson interval. It is more reliable than the normal approximation for small samples and for rates near 0 or 1.

**Cohort**: a group of players with the same registration week (`reg_week`).

**Segment**: a value of a categorical column used for breakdowns (channel `chnl`, country `geo`, age group `age_group`).

**`obs_end`**: the latest timestamp in the data: the maximum of `players.reg_ts` and `tzx.tzx_date`.

---

## 4. Input data

Two parquet files in the `DIR` folder (file names are set in `FILES`). Only the columns listed in `COLS` are read.

### `players_curr.parquet`: one row per player

| Column in file | Meaning | Name after renaming |
|---|---|---|
| `player_id` | Unique player identifier | `player_id` |
| `registration_ts` | Registration time | `reg_ts` |
| `gender` | Gender | `gender` |
| `status` | Player status | `player_status` |
| `age_group` | Age group | `age_group` |
| `country` | Country | `geo` |
| `acquisition_channel` | Acquisition channel | `chnl` |

`player_id` must be unique. Otherwise `build_ftd` fails on `merge(validate="one_to_one")`.

### `tzx_curr.parquet`: one row per transaction (all types and statuses)

| Column in file | Meaning | Name after renaming |
|---|---|---|
| `tx_id` | Transaction id, breaks ties in time | `tx_id` |
| `player_id` | Player | `player_id` |
| `type` | Transaction type (`deposit` is needed) | `type` |
| `currency` | Transaction currency | `currency` |
| `status` | Status (`success` is needed) | `tzx_status` |
| `payment_method` | Payment method | `paymthd` |
| `created_at` | Transaction time | `tzx_date` |
| `amount_eur` | Amount in EUR | `amount_eur` |
| `date` | Source column `date`; does not reach the final `ftd` | `date` (renamed to `fx_date` in `ftd_raw`) |

---

## 5. Configuration: `ftd_vars.py`

All constants live in one place. Analysis behaviour can be changed here without touching the code.

### Section 1. Data sources

| Variable | Type | What it does |
|---|---|---|
| `DIR` | `Path` | Folder with the parquet files. Taken from the `IG_CURR_DATA_DIR` environment variable, otherwise `<ig_roadmap>/curr_data` (next to the `ftd` folder) |
| `FILES` | `dict[str, str]` | File names: `{"players": "players_curr.parquet", "tzx": "tzx_curr.parquet"}`. The key (`players`, `tzx`) is the internal table name used in all dictionaries below |
| `TABLES` | `dict[str, Path]` | Full paths `DIR / file` per table. Used by `show_schema` |

### Section 2. Which columns to load

| Variable | What it does |
|---|---|
| `COLS` | Per table, the list of columns read from parquet (`pd.read_parquet(..., columns=...)`). Saves memory and time. A column missing from this list cannot be used in the pipeline |
| `CATEGORY_COLS` | Per table, the list of low-cardinality columns. After reading they are cast to `category` (saves memory). Listed columns that are not in the loaded dataframe are silently skipped |

### Section 3. Business rules

| Variable | Value | What it does |
|---|---|---|
| `DEPOSIT_TYPE` | `"deposit"` | The `type` value that counts as a deposit |
| `SUCCESS_STATUS` | `"success"` | The status value that counts as a completed transaction |
| `FTD_SORT_COLS` | `["tzx_date", "tx_id"]` | Sort order of a player's deposits. The first is the FTD, the second is the STD. Column names are given **after** the `TZX_RENAME_COLS` renaming |

### Section 4. Rename maps

| Variable | Applied to | What it does |
|---|---|---|
| `PLAYERS_RENAME_COLS` | Players table | `registration_ts → reg_ts`, `status → player_status`, `acquisition_channel → chnl`, `country → geo` |
| `TZX_RENAME_COLS` | Whole transactions table (before filtering deposits) | `created_at → tzx_date`, `status → tzx_status`, `payment_method → paymthd` |
| `FTD_RENAME_COLS` | First-deposit rows. Keys are names after `TZX_RENAME_COLS` | `tx_id → ftd_id`, `tzx_status → ftd_status`, `tzx_date → ftd_ts`, `date → fx_date`, `currency → ftd_curr`, `amount_eur → ftd_eur`, `paymthd → ftd_paymthd` |

### Section 5. Output layout

| Variable | What it does |
|---|---|
| `FTD_RAW_COLS` | Columns taken from `ftd_raw` when joining onto players: `player_id, ftd_id, ftd_ts, ftd_week, ftd_curr, ftd_eur, ftd_paymthd`. An explicit list prevents column name collisions in the merge (`country_x`/`country_y`) |
| `FTD_ALL_COLS` | Final set and order of columns of the `ftd` table (one row per player). Anything not listed here is dropped from `ftd`. To see an extra player column in `ftd` (for example `age_group`), add it here |

`age_cal_days` does not need to be in `FTD_ALL_COLS`: it is added in `run_pipeline` after `build_ftd`.

### Section 6. Analysis parameters

| Variable | Default | What it does |
|---|---|---|
| `CONV_WINDOWS` | `(0, 1, 3, 7, 14, 30)` | Registration → FTD conversion windows in calendar days. They become the columns `D0, D1, ...` |
| `REF_WINDOW` | `7` | Window for which the mature sample size and confidence interval are also shown (`n_D7`, `D7_lo`, `D7_hi`). Must be in `CONV_WINDOWS`, otherwise those columns are not added |
| `CONV_SEGMENTS` | `["chnl", "geo"]` | Segment columns for breakdowns in stages 2 and 3. A column must exist in `ftd` (that is, be in `FTD_ALL_COLS`). Missing columns are skipped |
| `CURVE_MAX_DAYS` | `30` | Length of the cumulative curve in days (points from 0 to this value) |
| `COHORT_MAX_WEEKS` | `8` | Number of weeks shown by the cohort triangle (columns `W0 ... W8`) |
| `TOP_N_SEGMENTS` | `8` | How many of the most frequent segment values to keep; the rest become `"other"` |
| `QUANTILES` | `(0.1, 0.25, 0.5, 0.75, 0.9, 0.95, 0.99)` | Quantiles in `grouped_summary` (columns `p10, p25, p50, ...`) |
| `STD_WINDOWS` | `(0, 1, 3, 7, 14, 30)` | FTD → STD conversion windows, calendar days after the first deposit. The last value is used as the reference window |
| `EARLY_VALUE_DAYS` | `7` | Early value window length: how many days after the FTD deposits are counted |

---

## 6. Core: `ftd_core.py`

Shared logic used by all stages. Imported as `import ig_roadmap.ftd.ftd_core as core`.

### `load_tables() -> dict[str, DataFrame]`

Reads all parquet files from `v.FILES`.

- Reads only the columns from `v.COLS`.
- Casts the columns from `v.CATEGORY_COLS` to `category`.
- Returns a dictionary `{"players": DataFrame, "tzx": DataFrame}` with the **original** column names (renaming is done by `run_pipeline`).
- If any file is missing, it raises `FileNotFoundError` with the full path right away. This shows the cause immediately instead of a confusing `KeyError` later in the code.

### `show_schema(files: dict) -> None`

For every parquet file, prints the row count, column count, size in MB and a "column, dtype" table.

- Reads only parquet metadata, not the data.
- Types come from the file's pandas metadata: `numpy_type` for ordinary columns (`object`, `int64`, `datetime64[ns]`), while columns with `pandas_type == "categorical"` are shown as `category`.
- Missing files are silently skipped.
- **Limitation:** pandas metadata exists only if the file was written through pandas (`df.to_parquet`). For a file written directly through pyarrow or exported from ClickHouse, `pandas_metadata` is `None` and the function fails with `TypeError`.

The `files` argument is a "name → path" dictionary, usually `v.TABLES`.

### `week_start(ts: Series) -> Series`

For every timestamp, returns Monday 00:00 of its week: `ts.dt.normalize()` minus the weekday in days. Works with timezone-aware columns; `NaT` stays `NaT`. Faster than `dt.to_period("W").dt.start_time`.

### `build_ftd_raw(deps: DataFrame) -> DataFrame`

Picks the first successful deposit of each player.

1. Sorts `deps` by `v.FTD_SORT_COLS`.
2. Keeps the first row per `player_id`.
3. Renames columns using `v.FTD_RENAME_COLS`.
4. Adds `ftd_week`, the Monday of the first deposit's week.

Returns one row per player who has at least one successful deposit. Players without deposits are not here.

### `build_ftd(players: DataFrame, ftd_raw: DataFrame) -> DataFrame`

Joins first deposits onto players (`left merge` on `player_id`, `validate="one_to_one"`) and computes derived fields:

| Column | Meaning |
|---|---|
| `has_ftd` | `True` if the player has a successful deposit |
| `ftd_lag_days` | Fractional days from registration to FTD |
| `ftd_lag_cal_days` | Calendar days from registration to FTD (0 means on the registration day). `float`, `NaN` means "no FTD" |

Returns a dataframe trimmed to the columns of `v.FTD_ALL_COLS`. If `player_id` is not unique on either side, it raises `pandas.errors.MergeError`.

### `run_pipeline(dfs=None) -> dict`

The main core function: load → rename → filter deposits → FTD → maturity.

The `dfs` argument is already loaded tables (the result of `load_tables`). If omitted, tables are loaded inside. The passed `dfs` are not modified.

Returns a dictionary:

| Key | Contents |
|---|---|
| `players` | Players with renamed columns and `reg_week` |
| `tzx` | **All** transactions (any type and status) with renamed columns |
| `deps` | Successful deposits only (`type == v.DEPOSIT_TYPE` and `tzx_status == v.SUCCESS_STATUS`). All deposits live here, not just the first ones |
| `ftd_raw` | First deposit of each paying player (result of `build_ftd_raw`) |
| `ftd` | One row per player: player data + first deposit + `age_cal_days` |
| `obs_end` | End of the observation period: `max(players.reg_ts.max(), tzx.tzx_date.max())` |

`age_cal_days` is the number of calendar days from a player's registration to `obs_end` (`obs_end.normalize() - reg_ts.normalize()`). It underlies the maturity rule: a player is mature for window Dn when `age_cal_days > n`.

### `validate_ftd(ftd: DataFrame) -> None`

Prints a quick quality check of the final table:

| Report line | What it counts |
|---|---|
| `players` | Total players |
| `players with FTD` | Players with an FTD and their share |
| `FTD before regs` | Cases where the FTD happened before registration (`ftd_lag_days < 0`), a sign of data problems |
| `null reg_ts` | Players without a registration time |
| `null chnl` | Players without an acquisition channel |
| `FTD amount <= 0` | FTDs with zero or negative amount (empty amounts are not counted) |

If `player_id` is duplicated, it raises `ValueError` before printing the other checks.

---

## 7. Stage 1: `ftd_inventory.py`

Data overview and FTD construction. Printing only, nothing is saved.

### `main() -> dict`

Runs in order:

1. **File inventory:** `core.show_schema(v.TABLES)`, the size and schema of each parquet file.
2. **Raw data:** loading via `core.load_tables()`, then:
   - first 7 rows of players and `info()` for them;
   - unique combinations of `status / type / payment_method` in transactions (up to 50 rows) and `info()` for them. This is a quick way to see which types, statuses and methods exist in the data at all.
3. **FTD build:** `core.run_pipeline(dfs)` (no second read of the files), then:
   - unique combinations of `tzx_status / type / paymthd` among the selected deposits;
   - unique combinations of `player_status / age_group` among players;
   - first rows of `ftd_raw` and `ftd`.
4. **Checks:** `core.validate_ftd(ftd)`.

Returns the `run_pipeline` result so the next stages do not read the data again.

---

## 8. Stage 2: `ftd_stat.py`

Statistics on the `ftd` table: FTD amount distribution, conversion, cumulative curve, cohorts, weekly dynamics. The functions are generic (age and lag columns are parameters), so `ftd_std.py` reuses them for the second deposit.

### 8.1. Confidence intervals

**`wilson_ci(successes, total, z=1.96) -> (lower, upper)`**
Wilson interval for a proportion. Accepts numbers or arrays. Returns two arrays: lower and upper bounds. Where `total == 0` the result is `NaN`. Formula:

```
p      = k / n
centre = (p + z²/2n) / (1 + z²/n)
half   = z · sqrt(p(1-p)/n + z²/4n²) / (1 + z²/n)
CI     = centre ± half
```

`z = 1.96` corresponds to 95%.

**`add_rate_ci(df, num, den, rate_col="rate") -> DataFrame`**
Returns a copy of `df` with three new columns: `rate` (= `num / den`, `NaN` when `den == 0`), `rate_lo`, `rate_hi`. Parameters: `num` is the numerator column name, `den` the denominator, `rate_col` the name of the rate column (the `_lo`/`_hi` suffixes are appended to it).

### 8.2. Segment helpers

**`resolve_by(df, by) -> (df, list[str])`**
Normalizes the `by` argument to a list of column names:
- `None` adds a constant column `all` with value `"all"` and returns `["all"]`. This lets totals and breakdowns use the same code;
- a string becomes `[string]`;
- a list becomes a copy of the list.

Idempotent: calling it again with its own output changes nothing.

**`collapse_rare(s, top_n=v.TOP_N_SEGMENTS) -> Series`**
Keeps the `top_n` most frequent values and relabels the rest as `"other"`. Missing values are replaced with `"(missing)"` before ranking, so reports show them as their own row instead of `groupby` silently dropping them. Returns an `object` series. If the data already has a real value `"other"`, it merges with the tail.

**`grouped_summary(df, value, by=None, quantiles=v.QUANTILES) -> DataFrame`**
Distribution summary of column `value` per group. Result columns: `n` (count of non-null values), `mean`, `std`, `p10 ... p99` (per `quantiles`), `max`. Empty values are ignored.

### 8.3. Window conversion

**`conversion_windows(df, by=None, windows=v.CONV_WINDOWS, age_col="age_cal_days", lag_col="ftd_lag_cal_days") -> DataFrame`**
Conversion in every window in "long" format, mature players only. For window n:
- `eligible` is rows with `age_col > n` (mature);
- `converted` is, among the mature, those with `lag_col <= n` (`NaN`, i.e. "never converted", counts as `False`).

Parameters: `df` is one row per player; `by` is `None`, a column name or a list; `windows` are window lengths in calendar days; `age_col` is calendar days from the start event to `obs_end`; `lag_col` is calendar days from the start event to the target event.

Result: columns `<by...>`, `eligible`, `converted`, `day` (the number n), `window` (the string `"D7"`), `rate`, `rate_lo`, `rate_hi`. Rows with a missing `by` value are dropped by `groupby`, so segments should be passed through `collapse_rare` first.

**`conversion_table(df, by=None, windows=v.CONV_WINDOWS, ref_window=v.REF_WINDOW, age_col=..., lag_col=...) -> DataFrame`**
Wide table: one row per segment. Columns:

| Column | Meaning |
|---|---|
| `players` | All players in the segment (immature included; this is just a count) |
| `D0, D1, D3, ...` | Conversion in each window, computed on mature players only |
| `n_D<ref>` | Mature sample size for the reference window |
| `D<ref>_lo`, `D<ref>_hi` | 95% Wilson interval for the reference window |

The columns `n_D...`, `*_lo`, `*_hi` appear only if `ref_window` is in `windows`. Rows are sorted by `players` descending. With `by=None` there is a single row `all`.

**`conversion_by_segments(df, segments=v.CONV_SEGMENTS, windows=v.CONV_WINDOWS, top_n=v.TOP_N_SEGMENTS) -> dict[str, DataFrame]`**
Runs `conversion_table` for every column in `segments` and returns a "column name → table" dictionary. Before the calculation the column's values are collapsed via `collapse_rare` (top N + `"other"`). Columns missing from `df` are skipped.

### 8.4. Cumulative curve

**`cumulative_curve(df, by=None, max_day=v.CURVE_MAX_DAYS) -> DataFrame`**
Cumulative conversion by day since registration. The index is the day 0..`max_day`, columns are segments, values are rates. Each point is computed on players mature for that day, so a later point is built on older cohorts than an earlier one. The curve shows where "ripening" ends and growth stops.

### 8.5. Weekly cohorts

**`cohort_triangle(ftd, max_weeks=v.COHORT_MAX_WEEKS) -> DataFrame`**
Cohort triangle of cumulative conversion. Rows are registration weeks (`reg_week`), columns are:
- `players`, the cohort size;
- `W0 ... W<max_weeks>`, cumulative conversion by the **end** of the k-th seven-day period after registration (that is, `ftd_lag_cal_days <= 7*(k+1) - 1`).

A cell is filled only if **every** player of the cohort was observed for the whole period (`min(age_cal_days) >= 7*(k+1)`). Otherwise it is `NaN`, which produces the triangle shape.

**`weekly_dynamics(ftd, obs_end) -> DataFrame`**
Registrations by registration week against FTDs by FTD week. The index is `week` (Monday). Columns:

| Column | Meaning |
|---|---|
| `registrations` | Registrations in the week |
| `ftds` | First deposits made in the week |
| `ftds_same_week_cohort` | Of those, from players registered in the same week |
| `ftds_older_cohorts` | Of those, from earlier cohorts (`ftds - ftds_same_week_cohort`) |
| `share_older` | Share of FTDs from older cohorts. A high value means the week's FTD volume depends on past acquisition, not on current traffic |
| `ftd_per_registration` | `ftds / registrations` |

Incomplete weeks are excluded: a week stays only if all 7 of its days were fully observed (`week + 7 days <= obs_end.normalize()`). Division by zero gives `NaN`.

### 8.6. `main(res=None) -> dict`

Prints the stage report: `ftd_eur` distribution (players with an FTD only), overall conversion, conversion for each segment, cumulative curve, cohort triangle, weekly dynamics. If `res` is not passed, it calls `core.run_pipeline()`. Returns `res`.

---

## 9. Stage 3: `ftd_std.py`

Behaviour after the first deposit: second deposit (STD) and early value. Only deposit data is used; retention and revenue need activity and bets tables and are not part of the package.

> The `ftd` table contains first deposits only. All other deposits are in `deps` (a key of the `run_pipeline` result). The functions of this module join the needed deposits from `deps` back onto `ftd` by `player_id`.

### `build_std(deps, ftd, obs_end) -> DataFrame`

One row per player **with an FTD**, with the second deposit attached.

1. Sorts `deps` by `v.FTD_SORT_COLS` (same as when choosing the FTD, so the first deposit matches the FTD).
2. Numbers deposits within each player (`dep_no`: 0 is the first, 1 is the second).
3. Takes the deposit with number 1 and joins it to `ftd` (`left merge`, `one_to_one`).

Added columns:

| Column | Meaning |
|---|---|
| `std_ts` | Time of the second deposit (`NaT` if none) |
| `std_eur` | Amount of the second deposit in EUR (`NaN` if none) |
| `has_std` | `True` if the player has a second deposit |
| `std_lag_days` | Fractional days from FTD to STD |
| `std_lag_cal_days` | Calendar days from FTD to STD (0 means the same day) |
| `ftd_age_cal_days` | Calendar days from the FTD to `obs_end` (basis of maturity for STD windows) |

### `std_conversion(ftd_std, by=None, windows=v.STD_WINDOWS) -> DataFrame`

Share of FTD players who made a second deposit within n days of the FTD. This is `conversion_table` from `ftd_stat` with:
- `age_col="ftd_age_cal_days"` (maturity measured from the FTD);
- `lag_col="std_lag_cal_days"`;
- `ref_window = windows[-1]` (the last window is the reference).

Only players whose FTD is older than n days enter window n. The result format is the same as `conversion_table`: `players`, `D0 ... D30`, `n_D30`, `D30_lo`, `D30_hi`.

### `early_value(deps, ftd, obs_end, days=v.EARLY_VALUE_DAYS) -> DataFrame`

Deposits made in the first `days` days after the FTD (the FTD itself is included in the window).

- Only players with an FTD older than `days` calendar days are kept, so every row has a full observation window.
- The window is measured by exact time: from `ftd_ts` inclusive to `ftd_ts + days` days exclusive. This differs from the conversion windows, which use calendar days.
- All successful deposits are summed (`amount_eur`).
- Segment columns come from `v.CONV_SEGMENTS` that exist in `ftd`.

Result: one row per player, columns `player_id`, `ftd_eur`, segment columns, `deposits` (number of deposits in the window), `deposit_eur` (their sum).

### `early_value_summary(ev, by=None) -> DataFrame`

Average early value metrics per segment (takes the output of `early_value`):

| Column | Meaning |
|---|---|
| `players` | Players in the segment |
| `avg_deposits` | Average number of deposits in the window |
| `avg_deposit_eur` | Average deposit amount in the window, EUR |
| `median_deposit_eur` | Median deposit amount in the window |
| `avg_ftd_eur` | Average FTD size |
| `repeat_share` | Share of players with two or more deposits in the window |
| `value_multiple` | `avg_deposit_eur / avg_ftd_eur`: how many "first deposits" a player brings in the window |

### `main(res=None) -> dict`

Prints the report: FTD → STD conversion overall, early value overall, then for each segment in `v.CONV_SEGMENTS` the second deposit conversion and early value (segment values are first collapsed via `collapse_rare`). If `res` is not passed, it calls `core.run_pipeline()`. Returns `res`.

---

## 10. Entry point: `__main__.py`

### `STAGES`

The tuple `("inventory", "stat", "std")`: the allowed stage names and their execution order.

### `main() -> None`

Reads stage names from the command line (`argparse`; with no arguments all stages run) and runs the selected ones in a fixed order:

```python
res = None
if "inventory" in chosen: res = inventory.main()
if "stat" in chosen:      res = stat.main(res)
if "std" in chosen:       res = std.main(res)
```

If `inventory` ran, its `res` is passed on and the data is not read again. If only `stat` or `std` is run, `res` is `None` and the stage calls `core.run_pipeline()` itself. An unknown stage name produces an `argparse` error listing the valid ones.

---

## 11. Dataframe column reference

### `ftd`: one row per player

| Column | Type | Meaning |
|---|---|---|
| `player_id` | n/a | Player identifier |
| `reg_ts` | datetime | Registration time |
| `reg_week` | datetime | Monday of the registration week (cohort) |
| `ftd_ts` | datetime | First deposit time (`NaT` if no FTD) |
| `ftd_week` | datetime | Monday of the first deposit week |
| `ftd_lag_days` | float | Fractional days from registration to FTD |
| `ftd_lag_cal_days` | float | Calendar days from registration to FTD, `NaN` means no FTD |
| `has_ftd` | bool | Whether the player has a successful deposit |
| `geo` | category | Country |
| `chnl` | category | Acquisition channel |
| `ftd_id` | n/a | FTD transaction id |
| `ftd_curr` | category | FTD currency |
| `ftd_eur` | float | FTD amount in EUR |
| `ftd_paymthd` | category | FTD payment method |
| `age_cal_days` | int | Calendar days from registration to `obs_end` (added in `run_pipeline`) |

Plus any extra columns you added to `FTD_ALL_COLS` (for example `age_group`). For players without a deposit all `ftd_*` columns are empty.

### `ftd_std` (result of `build_std`): one row per player with an FTD

All `ftd` columns plus `std_ts`, `std_eur`, `has_std`, `std_lag_days`, `std_lag_cal_days`, `ftd_age_cal_days` (described in [section 9](#9-stage-3-ftd_stdpy)).

### Long window table (`conversion_windows`)

`<by...>`, `eligible`, `converted`, `day`, `window`, `rate`, `rate_lo`, `rate_hi`.

---

## 12. Common errors

| Symptom | Cause | What to do |
|---|---|---|
| `ModuleNotFoundError: No module named 'ig_roadmap'` | Not run from the package root | Run from the folder containing `ig_roadmap` with `python -m ig_roadmap.ftd`. Or add that folder to `PYTHONPATH`, or install the project with `pip install -e .` |
| `ModuleNotFoundError: ...project_core` | The external module `ig_roadmap/project_core.py` is missing | Add a module with the functions `print_df()` and `title()` |
| `FileNotFoundError: Source file not found: ...` | A parquet file is missing at the path from `DIR`/`FILES` | Check `DIR` and `IG_CURR_DATA_DIR`, and the file names in `FILES` |
| `TypeError: 'NoneType' object is not subscriptable` in `show_schema` | The parquet was not written through pandas, so there is no `pandas_metadata` | Write the file with `df.to_parquet`, or read the schema from `pf.schema_arrow` |
| `pandas.errors.MergeError` in `build_ftd` or `build_std` | Duplicate `player_id` | Check player uniqueness in `players_curr`; `validate_ftd` prints the duplicate count |
| `KeyError` on a column from `COLS` | The column is not in the parquet file | Fix `COLS` or the file schema |
| A needed column is missing from `ftd` (`KeyError` in segments) | The column is not in `FTD_ALL_COLS` | Add it to `FTD_ALL_COLS`; segments from `CONV_SEGMENTS` that are not in `ftd` are skipped without an error |
| The conversion table lacks `n_D7`, `D7_lo`, `D7_hi` | `REF_WINDOW` is not in `CONV_WINDOWS` | Make the values consistent |
| A window's conversion is `NaN` | No mature players for the window (observation period shorter than the window) | Use a smaller window or wait for more data |
| The last two weeks are absent from `weekly_dynamics` | Incomplete weeks are excluded on purpose | See the maturity rule in [section 3](#3-key-concepts) |

---

## 13. Extending the package

- **New segment:** add the column to `FTD_ALL_COLS` (if it is not in `ftd` yet) and to `CONV_SEGMENTS`. Both stages pick it up automatically.
- **Different windows:** change `CONV_WINDOWS`, `STD_WINDOWS`, `REF_WINDOW`, `EARLY_VALUE_DAYS`, `CURVE_MAX_DAYS`, `COHORT_MAX_WEEKS` in `ftd_vars.py`.
- **Different definition of a deposit or success:** `DEPOSIT_TYPE` and `SUCCESS_STATUS`.
- **Third and later deposits:** `build_std` already computes the deposit sequence number (`dep_no`); take `dep_no == 2` and so on following the same pattern.
- **New stage:** create a module `ftd_<name>.py` with a function `main(res=None) -> dict` modelled on `ftd_stat.py`, then add its name to `STAGES` and a run branch in `__main__.py`.
- **Retention and revenue metrics:** these need activity and bets data and are not part of this package.
