# Crush-prediction pipeline

These tools build a dataset of simulated crowd crushes and train models on it. A model looks at what is happening on a walkway right now (how many people are on it, how fast they move, what is flowing in from behind, what is waiting ahead, and how all of that changed over the last minute) and predicts the highest crushing force on that walkway over the next few minutes. If the prediction reaches the danger line, that is an alarm.

Everything runs on top of [CrowdWalk](https://github.com/crest-cassia/CrowdWalk) with the CrushWalk physics addon from this repository. You don't need to write any Java. The tools generate the maps, run the simulator, collect the results and train the models for you.

The scripts are all in this folder, in five groups:

| Group | Script | What it does |
|---|---|---|
| Data collection | `run_batch.py` | The runner: makes maps, simulates them, saves the results, unattended. |
| | `make_maps.py` | Makes random walkway layouts and crowds. `run_batch.py` calls it for you. |
| | `link_context.py` | Turns one simulation's output into rows in the database. `run_batch.py` calls it for you. |
| Model training | `force_model.py` | The runner: trains the models on the database and tells you how accurate they are. |
| | `sweep.py` | Calls `force_model.py` at every window length and training size. |
| | `history_sweep.py` | Calls `force_model.py` once per history look-back, same data and window, and prints the table to choose from. |
| Main models | `models_main.py` | Random Forest and XGBoost with history (how each link changed over the last 10, 30 and 60 s). Trained by default. |
| Baselines | `baselines.py` | What the models are compared with: the training average, "no change", and Holt's time-series method. Always computed. |
| Other models | `models_other.py` | Comparison models: the main two without history, Ridge, and a density formula. |
| Reading results | `sweep_plot.py`, `error_bars.py` | Graphs and 95% ranges from saved results. They train nothing. |
| Helper | `background.py` | Lets the long jobs (`run_batch.py`, `force_model.py`, `sweep.py`, `history_sweep.py`) run in the background with a live log. |
| | `results.py` | Decides where results go: one dated folder per run, in one results folder. |

The other files in this folder (`CrushAgent.rb`, `prop.json`, `gen.json` and so on) are the simulator side: the agent scripts and the template settings every generated scenario copies. `PHYSICS_NOTES.md` explains the physics.

## Contents

1. [What you need](#1-what-you-need)
2. [Long jobs: the live log, Ctrl-C and stopping](#2-long-jobs-the-live-log-ctrl-c-and-stopping)
3. [Your first run](#3-your-first-run)
4. [Collecting a real dataset](#4-collecting-a-real-dataset)
5. [Training the models and reading the results](#5-training-the-models-and-reading-the-results)
6. [Choosing the window and checking the data size](#6-choosing-the-window-and-checking-the-data-size)
7. [Add-ons: what to add to a command](#7-add-ons-what-to-add-to-a-command)
8. [Changing what is inside the files](#8-changing-what-is-inside-the-files)
9. [When something goes wrong](#9-when-something-goes-wrong)
10. [How it works](#10-how-it-works)
11. [Option reference](#11-option-reference)

Every command below is run from the `crowdwalk/` folder.

**Where results go.** Training results do not land in the folder you are in. Each run gets its own folder inside one results folder, with the date in front of the name you give:

```
--out-dir fm_h120      ->   /mnt/ssd_1tb/crush/results/2026-10-12_fm_h120
```

So the folders sort in the order they were made, and the same name on a later day never overwrites an older result. When a later command reads a result, the short name is enough: `fm_h120/pressure/importance.csv` is looked up in the results folder, newest date first, and the path it picked is printed. To read an older one, give its dated name (`2026-10-10_fm_h120/...`). To write somewhere else, give a path with a folder in it (`--out-dir out/fm_h120` or `--out-dir /tmp/test`). A leading `./` does not count: `./fm_h120` is the same as `fm_h120`.

When `force_model.py`, `sweep.py` or `sweep_plot.py` finishes, it also writes one zip into the folder, named after it (`2026-10-12_fm_h120/2026-10-12_fm_h120.zip`), holding every graph and table. That is the one file to copy to another machine. The large per-row `predictions.csv` files are left out.

The results folder is set at the top of `results.py` (`ROOT`). On another machine, change it there, or run `export CRUSH_RESULTS=/some/folder` first.

## 1. What you need

- A Linux machine with CrowdWalk built from this repository (see the main [README](../README.md) for building). Simulations are slow, so use your fastest machine with plenty of disk space.
- Python 3.9 or newer, plus five packages:

  ```
  pip install pandas scikit-learn xgboost duckdb matplotlib
  ```

- These two environment variables, which CrowdWalk expects:

  ```
  export LANG=ja_JP.UTF-8
  export JAVA_OPTS='-Dgroovy.source.encoding=UTF-8 -Dfile.encoding=UTF-8'
  ```

To check the Python side is ready, run the self-tests. They use made-up data and don't touch the simulator, so they take a few minutes in total:

```
cd Crush3 && for s in link_context make_maps run_batch background baselines models_main force_model sweep sweep_plot error_bars; do python3 $s.py --selftest || break; done; cd ..
```

Each one should end with `selftest ok`. The loop stops at the first one that fails.

## 2. Long jobs: the live log, Ctrl-C and stopping

`run_batch.py`, `force_model.py` and `sweep.py` can run for hours. By default each one starts itself in the background, writes everything it prints to a log file, and shows that log live in your terminal. The first lines tell you where the log is and how to end the job:

```
running in the background (pid 41234), log: /mnt/ssd_1tb/crush/results/2026-10-12_fm_sweep/run.log
  Ctrl-C stops showing the log; the job keeps running
  end the job: python3 Crush3/sweep.py --stop --log /mnt/ssd_1tb/crush/results/2026-10-12_fm_sweep/run.log
```

| You want to | Do this |
|---|---|
| Stop watching, keep the job running | Press Ctrl-C. You can also close the terminal or log out. |
| Watch again | `tail -f <log>` with the path from the first lines |
| End the job | `python3 Crush3/sweep.py --stop --log fm_sweep/run.log` (use the script and log of your job; the short name finds the newest one) |
| Run it the plain way, where Ctrl-C ends the job | Add `--foreground` to the command. |
| Put the log somewhere else | Add `--log some/file.log` to the command. |

The default log is `<out>/run_batch.log` for `run_batch.py` and `<out-dir>/run.log` for the other two. A new job adds to the end of an existing log; it does not erase it. While a job is running, a second job with the same log file is refused, so give it another `--out-dir` or `--log`.

`--stop` does what Ctrl-C does in the foreground: `run_batch.py` ends the simulations in progress, keeps every finished run, and says so in the log. If a job has not ended after 60 seconds, `--stop` kills it.

## 3. Your first run

Start small. This makes five scenarios (one map from each family, one crowd each), runs them, and saves the results:

```
python3 Crush3/run_batch.py --runs 5 --n 1 --runs-per-map 1 --out ~/crush_test/maps --log-root ~/crush_test/logs --db ~/crush_test/crush.duckdb
```

While it works you'll see when each simulation starts, a progress line every 30 seconds, and a line when it finishes:

```
[seed_0] 5 runs generated, 0 already in DB or not needed, 5 to simulate
  started seed_0/corridor_000/run_0
    ... seed_0/corridor_000/run_0: running 1m30s, simulated 540/3600 s (15%)
    ... seed_0/corridor_000/run_0: running 2m00s, simulated 720/3600 s (20%)
  ...
  [1/5] seed_0/corridor_000/run_0: loaded (9m52s)
  started seed_0/funnel_000/run_0
  ...
loaded 5 new run(s) into /home/you/crush_test/crush.duckdb (target 5)
```

(The times are only an example. They depend on the map and the machine.) "Simulated 540/3600 s" means the run has reached minute 9 of its 60 simulated minutes.

When it finishes, check two things:

1. Open `~/crush_test/maps/runner_log.csv`. Every row should say `loaded`. If any say `failed`, the `message` column says why. See [When something goes wrong](#9-when-something-goes-wrong).
2. Look inside one scenario folder, for example `~/crush_test/maps/seed_0/corridor_000/`. It holds the map (`Map.xml`), and `run_0/` holds the crowd settings and `sim.log`, the simulator's own output for that run.

If both look right, the setup works.

## 4. Collecting a real dataset

A model needs far more than five runs. A reasonable first dataset is a couple of hundred:

```
python3 Crush3/run_batch.py --runs 200 --out /mnt/ssd_2tb/gen_maps --log-root /mnt/ssd_2tb/gen_logs --db /mnt/ssd_2tb/crush.duckdb
```

This can take many hours. It runs in the background (section 2), so you can press Ctrl-C or close the terminal and it carries on. If your machine has spare CPU cores, `--jobs 4` runs four simulations at the same time.

**Stopping and resuming.** End it with `--stop --log <log>` at any time. Every finished run is already safe in the database. When you start the same command again, it skips the runs it already has. Note that `--runs` counts *new* runs, so if you stopped after 120 of 200, restart with `--runs 80`.

**More data later.** The same seed always makes the same maps and crowds. To add new ones to an existing database, start from a seed you have not used: `--start-seed 3 --batches 2` runs seeds 3 and 4.

**What you get.** Each run is a different combination of:

- a map from one of five families: a corridor with a bottleneck, a funnel where several paths merge, a T-junction, a crossroads (sometimes with people walking both ways), or a branching tree like a stadium exit;
- a crowd: how many people (300 to 3000), when each group starts walking, and how quickly they are released.

Every map is used for three different crowds (change this with `--runs-per-map`). That matters for testing, as explained in the next section.

If you never want to type the paths, set them once in the SETTINGS block at the top of `run_batch.py` and run `python3 Crush3/run_batch.py` with no options (section 8).

## 5. Training the models and reading the results

The command below is the main test. It predicts the highest pressure over the next 120 ticks (2 minutes; one tick is one simulated second):

```
python3 Crush3/force_model.py /mnt/ssd_2tb/crush.duckdb --horizon 120 --cv 5 --target pressure --out-dir fm_h120
```

### The three tests worth running

| Add to the command | What it tests |
|---|---|
| `--cv 5` | Can the model predict a **new crowd on a layout it has seen**? Runs are split five ways; every run is tested once, and the model never sees the run it's being tested on. |
| `--holdout-map all` (instead of `--cv 5`) | Can the model handle a **layout it has never seen**? Every map is held out in turn, with all its runs. |
| `--cv 5 --observable-only` | Can it work from **things a camera could measure**? This hides the simulated forces from the model (in real life nobody can measure the force inside a crowd) and keeps counts, density, speeds and the layout. |

Expect the second and third to score worse than the first. How much worse is a useful result in itself.

### What gets trained

- **The main models** (`models_main.py`): `RF_history` and `XGB_history`. These are trained unless you say otherwise.
- **The baselines** (`baselines.py`), always:
  - `baseline: train mean` always guesses the average of the training runs.
  - `baseline: now` guesses that nothing changes: the value stays what it is now.
  - `Holt` looks only at the link's own past pressure, follows its level and its trend, and extends them over the window. It is the "pressure is rising, so it will keep rising" guess.
- **The other models** (`models_other.py`), only with `--models other` or `--models all`: `RandomForest` and `XGBoost` without history, `Ridge` and `Ridge_history` (straight-line models), and `Formula` (density times speed variance).

### What can be predicted

Each target has its own model and its own results folder:

- **`pressure`**: the highest pressure on the link during the window, in newtons. Results go in `<out-dir>/pressure/`.
- **`held`**: the longest time anyone on the link has been held above the crush threshold (1112 N) during the window, in seconds. 240 s means someone is crushed. Results go in `<out-dir>/held/`. This needs `time_over_threshold` in `link_logging.fields`; without it, `held` is skipped.

Without `--target`, both are run.

### Reading the output

Each test prints its results and saves the same thing as CSV files.

**`accuracy.csv`: how close are the predictions?** Two error measures, in newtons for `pressure` and seconds for `held`. Lower is better:

- **MAE** (mean absolute error): on average, how far off the prediction is.
- **RMSE** (root mean squared error): like MAE, but punishes big misses more.

Each comes as a mean and a spread across the test folds. A large spread means the result depends a lot on which runs ended up in the test set. A model is only useful if it beats `baseline: now`.

**`crossings.csv`: would it have warned in time?** (pressure only.) An *event* is the first time a link in a run reaches the danger line (`--threshold`, 1062 N by default). An *alarm* is a prediction at or above that line while the link is still below it. One row per model and for `Holt`:

| Column | Meaning |
|---|---|
| `events`, `events_warned`, `warned_share` | How many events there were, and how many had at least one alarm on that link in the window before them. |
| `median_warning_s` | The typical warning time: seconds between the first alarm and the event. Example: a link first reaches 1062 N at 8:20 of the run and the model's first alarm for it came at 7:15, so the warning was 65 s. |
| `alarms`, `alarms_real_share` | How many alarms were raised, and the share that were followed by a real crossing. The rest are false alarms. |
| `crossings`, `caught`, `caught_share` | The same idea counted per record instead of per event. |

The two plain baselines are not in this table: neither can ever raise an alarm on a calm link.

**`importance.csv`: which variables matter?** For each input, how much worse the best model gets when that input is scrambled. Big numbers are important variables. Numbers near zero (or negative) are candidates to drop. Inputs that carry the same information share their importance, so the real test of a short list is to retrain on it (`--top-features`, section 7). Columns ending in `_d10`, `_d30`, `_d60` are the history: the change over the last 10, 30 and 60 ticks.

**`error_by_band.csv`: where does it break down?** The error split by how large the real value was. The top rows (the highest forces) are the ones that matter for crushes. A negative `mean_bias` there means the model underestimates dangerous moments, which is the worst kind of mistake for this job.

**`predictions.csv`** has every tested row with its real value and every model's and baseline's prediction, if you want to make your own plots. `error_bars.py` reads this file.

## 6. Choosing the window and checking the data size

How far ahead should the model look, and is there enough data? `sweep.py` answers both by running the pressure test at several window lengths and several training sizes:

```
python3 Crush3/sweep.py /mnt/ssd_2tb/crush.duckdb --runs 12,24,48,72,0 --out-dir fm_sweep
```

It writes `fm_sweep/results.csv`, one row per window, training size and model, with the `crossings.csv` columns from section 5. The table is rewritten after every run, so a stopped sweep still leaves what it finished.

Then draw the graphs. This trains nothing and prints every number it plots:

```
python3 Crush3/sweep_plot.py fm_sweep/results.csv
```

- `sweep_data_<model>.png`: does more data help? One line per window, crush runs used for training along the bottom, warning time up the side. A line that has gone flat has enough data.
- `sweep_warning.png`: warning time by window, one bar per model.
- `sweep_alarms.png`: share of alarms that were real, by window.

To see how much those numbers could move by chance, add 95% ranges. This also trains nothing: it re-draws the test runs 2,000 times from the saved predictions.

```
python3 Crush3/error_bars.py /mnt/ssd_2tb/crush.duckdb fm_sweep --out fm_errorbars/results.csv
python3 Crush3/sweep_plot.py fm_errorbars/results.csv
```

The bar graphs then have a black line through each bar, and the data-size graphs a vertical line through each point. Two bars or points whose lines overlap a lot are not clearly different.

To compare every model and baseline in one table, trained on all runs:

```
python3 Crush3/sweep.py /mnt/ssd_2tb/crush.duckdb --models all --runs 0 --out-dir fm_models
```

### How far back should the history go?

`sweep.py` changes the data (window, number of runs) and keeps the inputs. `history_sweep.py` does the opposite: same data, same window, and only the history inputs change. It trains the two main models six times, each time letting them see further back: no history, then 10, 30, 60, 120 and 300 s.

```
python3 Crush3/history_sweep.py /mnt/ssd_2tb/crush.duckdb --out-dir fm_history
```

The table at the end has one row per look-back and model. The script does not choose for you: you read the table and decide.

Each row shows its own numbers and the difference to the row above it, which is the gain from looking that much further back. The difference is taken on the same test runs. Say 60 s of history gives a 70 s warning and 120 s gives 75 s: the difference is +5 s. The test runs are re-drawn 2,000 times, both are scored on the same re-draw each time, and that gives a range for the difference. If the range is −4 to +12 s it includes zero, so 120 s is not clearly better than 60 s.

How to read it (rule 2 changed to the one-standard-error rule on 2026-10-15):

1. **Events missed come first.** A look-back that misses more events is not an improvement, whatever else it gains. A missed event is worse than a false alarm.
2. **One-standard-error rule** (Hastie, Tibshirani & Friedman, *The Elements of Statistical Learning*). Among the look-backs that miss the fewest events, find the best row. Take the shortest look-back whose warning time and share of real alarms are both no more than one standard error below the best row's. The standard error is how much the best row's number moves when the test runs are re-drawn. Example: the best share of real alarms is 77% with a standard error of 6 points, so anything from 71% up counts as just as good, and the shortest such look-back wins. The table marks these rows `wr` in the `1se` column.
3. **Confirm it.** The choice was made on these test runs, so check it once on runs that played no part in it, such as a later seed.

With `--single`, each look-back gets only its own change (60 = the 60 s change alone) instead of all the shorter ones as well. Any list works with `--back`, for example every 10 s: `--back 0,10,20,30,40,50,60`.

With `--search both`, the `--back` list is a pool and the script tries sets of them together, such as 30+60 without 10. Forward search starts with no history and adds the look-back that helps most (fewest missed events, then the most real alarms) until all are in. Backward search starts with all of them and drops the one that hurts least until one is left. Each model gets its own path. The one-standard-error rule is then applied over the sets on both paths, and `pick` marks the passing set with the fewest look-backs. A pool of 6 takes 31 trainings per model.

```
python3 Crush3/history_sweep.py /mnt/ssd_2tb/crush.duckdb --search both --back 4,10,30,60,120,300 --out-dir fm_combo
```

| Column | Meaning |
|---|---|
| `back_s` | How far back the model may look, in seconds. 0 = no history. |
| `missed` | Events the model gave no warning for, out of all events. |
| `warning_s`, `real_%` | Typical warning time and share of alarms that were real: the same numbers as in `crossings.csv`. |
| `d_warning_s`, `d_real_%` with `lo`, `hi` | This row minus the row above it, and the 95% range of that difference. |
| `se_w`, `se_r` | Standard error of the warning time (s) and of the share of real alarms (points). |
| `1se` | `w` / `r`: within one standard error of the best row on warning time / real alarms. `wr` = both. |

The search is meant for one window (2 min by default, `--window` changes it). For another window, compare only your choice with today's setting, for example `--back 60,120`, instead of repeating the whole search.

To use your choice, set `LAGS` in `models_main.py` (section 8).

## 7. Add-ons: what to add to a command

Every option is something you add to the end of a command. They can be combined. The full list with defaults is in section 11; this is the short version by what you want to do.

### `force_model.py` (and `sweep.py` where marked)

| You want to | Add | Example |
|---|---|---|
| Look further or less far ahead | `--horizon N` (ticks) | `--horizon 300` for 5 minutes |
| Train only the pressure model | `--target pressure` | |
| Finish faster | `--no-importance` | Skips `importance.csv`, the slow part. |
| Test on layouts never seen | `--holdout-map all` in place of `--cv 5` | |
| Use only what a camera could see | `--observable-only` | |
| Compare with the other models (also `sweep.py`) | `--models all` | `--models other` trains only the comparison models. |
| Train on the strongest inputs only | `--top-features K --features-from <importance.csv>` | `--top-features 10 --features-from fm_h120/pressure/importance.csv --no-importance --out-dir fm_h120_top10` |
| See what less data does | `--train-runs N` | `--train-runs 24` trains each fold on 24 runs. |
| Move the danger line (also `sweep.py`) | `--threshold N` (newtons) | `--threshold 900` |
| Predict one exact moment, not the highest in the window | `--at-horizon` | The old target. |
| Change the number of test folds (also `sweep.py`) | `--cv K` | `--cv 10` |
| Name the results (also `sweep.py`) | `--out-dir NAME` | Becomes `<results folder>/<date>_NAME`. Use a new name for every experiment: the same name on the same day is the same folder. |

A typical longer command, made of add-ons:

```
python3 Crush3/force_model.py /mnt/ssd_2tb/crush.duckdb --horizon 120 --cv 5 --target pressure --models all --no-importance --out-dir fm_h120_all
```

### `sweep.py`

| You want to | Add | Example |
|---|---|---|
| Try other windows | `--windows A,B,C` (ticks) | `--windows 120,300,600` |
| Try other training sizes | `--runs A,B,C` (runs per fold, 0 = all) | `--runs 12,24,48,72,0` |
| Skip the data-size question | `--runs 0` | One training size: all runs. |
| Redraw its own graphs only | `--replot fm_sweep/results.csv` | Trains nothing. |

### `run_batch.py`

| You want to | Add | Example |
|---|---|---|
| Stop after a number of new runs | `--runs N` | `--runs 200` |
| Run whole seeds instead | `--batches N` | `--batches 2`; `--batches 0` keeps going until stopped. |
| Add new maps to an existing database | `--start-seed N` | `--start-seed 3 --batches 2` |
| More or fewer maps per family | `--n N` | `--n 4` gives 4 maps in each of the 5 families per seed. |
| More or fewer crowds per map | `--runs-per-map N` | `--runs-per-map 3` |
| Only some map families | `--families ...` | `--families corridor funnel` |
| Several simulations at once | `--jobs N` | `--jobs 4` |
| Shorter or longer simulations | `--sim-minutes N` | `--sim-minutes 30` |
| Give slow runs more time | `--timeout SECONDS` | `--timeout 7200` |
| Fewer progress lines | `--progress-every SECONDS` | `--progress-every 300`, or `0` for none. |
| Save disk space | `--drop-agent-log` | Deletes each run's per-person log after loading. |
| Choose where things go | `--out`, `--log-root`, `--db` | See section 4. |

The runs per seed are `--n` × number of families × `--runs-per-map`: with the defaults, 4 × 5 × 3 = 60.

### `sweep_plot.py` and `error_bars.py`

| You want to | Add | Example |
|---|---|---|
| Plot only some models | `--models A,B` | `--models RF_history,XGB_history,Holt` |
| Ranges for several result folders at once | more folders after the database | `error_bars.py DB fm_sweep fm_models --out fm_errorbars/results.csv` |
| Ranges for another danger line | `--threshold N` | Must be the threshold the sweep used. |

### The three long jobs

| You want to | Add |
|---|---|
| Run in the terminal, Ctrl-C ends it | `--foreground` |
| Choose the log file | `--log FILE` |
| End a running job | `--stop --log FILE` (nothing else needed) |

## 8. Changing what is inside the files

Some things are not options because they rarely change. Each is one clearly marked place in one file. After changing a file, run its self-test (`python3 Crush3/<file> --selftest`).

**The main models' settings.** In `models_main.py`, `random_forest()` and `xgboost()` hold the settings (number of trees, depth, learning rate). Change the numbers there. `models_other.py` uses the same two functions for the models without history, so both stay in step.

**What counts as history.** In `models_main.py`:

```python
LAGS = (10, 30, 60)   # ticks back
LAG_COLS = ("density", "agent_count", ...)
```

`LAGS` is how far back to compare; `(10, 30, 60, 120)` adds a two-minute look back. `history_sweep.py` tests which look-back is enough (section 6). `LAG_COLS` is which columns get a history. Only add columns where an empty link really means 0.

**Adding a model to compare.** In `models_other.py`, add one line to `new_models()`. The name is what appears in every table; the second value says whether the model gets the history columns:

```python
from sklearn.ensemble import ExtraTreesRegressor
...
    models = {"RandomForest": (random_forest(), False), "XGBoost": (xgboost(), False),
              "Ridge": (ridge(), False), "Ridge_history": (ridge(), True),
              "ExtraTrees": (ExtraTreesRegressor(n_estimators=300, n_jobs=-1, random_state=0), True)}
```

Anything with `fit(X, y)` and `predict(X)` works. It is then trained with `--models other` or `--models all`. To make a model one of the main ones, add it to `new_models()` in `models_main.py` instead.

**The Holt baseline.** In `baselines.py`, `HOLT_GRID` lists the smoothing settings that are tried; the best one for the training runs is used and printed in the log. To add a baseline, add it to `predictions()` in the same file.

**Which columns are "sim-only".** In `force_model.py`, `SIM_ONLY` lists the words that mark a column as something no camera could measure. `--observable-only` drops every column whose name contains one of them.

**Default paths and sizes for data collection.** The SETTINGS block at the top of `run_batch.py` holds every default: how many runs, which families, where the maps, logs and database go, how long a simulation lasts. Edit it once and `python3 Crush3/run_batch.py` needs no options. An option on the command line still wins for that run.

**Crush rule.** In `Crush3/prop.json`:

```
"crushThreshold": 1112.0,   // newtons of compression that count as dangerous
"crushMargin": 50.0,        // once counting, dips this far below still count
"crushDuration": 240.0,     // seconds above the threshold, in a row, before an agent is crushed
```

The 1112 N held for 4 minutes comes from Kroll et al. (2017), who studied fatal chest-compression forces. New scenarios copy these values when they are generated, so change them before running `run_batch.py`. The default `--threshold` of 1062 N is the crush threshold minus the margin; if you change the rule, pass the matching `--threshold`. `pressureTransfer` in the same file sets how high the pressure in a queue can get; read `PHYSICS_NOTES.md` before touching it.

**What the simulator logs.** `link_logging.fields` in `Crush3/prop.json` lists the per-link values the simulator writes. A new field becomes a new column in the database and a new model input for runs collected after the change.

**Map sizes and crowds.** If your runs never reach dangerous force levels, the walkways are probably too wide or the crowds too small. The ranges are at the top of each map-family function in `make_maps.py` (`corridor`, `funnel`, `tjunction`, `crossroads`, `tree`), and the crowd ranges are in `make_gen`.

Runs collected before and after a change to the physics, the crush rule or the map ranges should not share a database. Give `--db` a new file.

## 9. When something goes wrong

| What you see | What to do |
|---|---|
| Every run `failed` with `exit code 1` | Open any run's `sim.log`. Usually Java isn't found, CrowdWalk isn't built, or the environment variables from section 1 aren't set. |
| `no linkMetrics.csv at ...` | The simulation ran but wrote no link log. Check that `link_logging` is still in `Crush3/prop.json`, and that the `--log-root` folder is writable. |
| `timeout after 3600s` | A run took over an hour of real time and was stopped. Raise `--timeout` if your runs are just slow. |
| `10 runs failed in a row -- stopping` | This safety stop catches a broken setup. Fix the cause from `sim.log`, then run the same command again. |
| `--holdout-map needs runs on at least 2 maps` | The database only has one layout. Collect more runs first. |
| `--cv 5 needs at least 5 runs` | Same: not enough runs yet. |
| Maximum force never gets near 1112 N | The crowds aren't dense enough to crush. See *Map sizes and crowds* in section 8. |
| `a job is already running with this log` | The earlier job with the same `--out-dir` is still going. Watch it with `tail -f`, end it with `--stop --log`, or start the new one with another `--out-dir`. |
| `no job is running for ...` after `--stop` | It had already finished. The end of the log shows how. |
| The log shows nothing for minutes right after a training job starts | Normal on a large database: the history and Holt columns are built first, and no line is printed while that happens. |
| `Killed`, or `MemoryError` | The table with the history and Holt columns did not fit in memory. Use a machine with more memory, or make the table smaller: fewer entries in `LAGS` (`models_main.py`) or in `HOLT_GRID` (`baselines.py`). |

To look inside the database yourself:

```
python3 -c "import duckdb; print(duckdb.connect('/mnt/ssd_2tb/crush.duckdb', read_only=True).sql('SELECT map_file, COUNT(DISTINCT scenario_id) AS runs, MAX(max_compression_pressure) AS peak_N FROM link_ticks GROUP BY 1 ORDER BY 1'))"
```

## 10. How it works

For each seed, `run_batch.py` does this:

```
make_maps.py      make a set of maps, and several crowds for each map
      │
      ▼
CrowdWalk         simulate one run at a time (quickstart.sh, no GUI)
      │           writes linkMetrics.csv: one row per walkway per tick
      ▼
link_context.py   add context to every row and store it in the database,
                  then delete the CSV
```

Then it moves on to the next seed, which means new maps and new crowds.

The simulator's link log says what is happening on each walkway (a "link" in CrowdWalk). `link_context.py` adds what is happening around it:

- **Previous links:** how many people are arriving from the walkways that feed into this one, and how fast.
- **Next links:** how crowded the walkways ahead are, and whether they get narrower.
- **Direction:** how many people walk each way, and how fast. Two groups pushing against each other is a known crush risk.
- **Layout:** the walkway's length, width and area, from the map.

Simulation settings such as the crush threshold are deliberately left out of the database. A real camera feed wouldn't have them, so a model that depended on them would be useless in the real world.

Each run is stored with the map it used (`map_file` is a readable name, `map_id` a fingerprint of the map file). That is how `--holdout-map` knows which runs to hide together.

Training goes through one place:

```
sweep.py          for every window and training size ...
      │
      ▼
force_model.py    add the history and Holt columns, split the runs into folds,
      │           train, predict the held-out runs, score
      ├── models_main.py    the models it trains by default
      ├── models_other.py   the models added by --models other / all
      └── baselines.py      train mean, no change, Holt
      │
      ▼
accuracy.csv, crossings.csv, predictions.csv ...   one folder per test
      │
      ▼
error_bars.py, sweep_plot.py    ranges and graphs, read from those files
```

Rows are never split at random. Neighbouring ticks of one run are nearly the same, so a random split would put the answer in the training data. Whole runs (or whole maps) are held out instead.

## 11. Option reference

### `run_batch.py`

The defaults are whatever the SETTINGS block at the top of the file says; the values below are the ones it ships with.

| Option | Default | Meaning |
|---|---|---|
| `--runs N` | | Stop once N new runs are saved. Failed runs don't count. Overrides `--batches`. |
| `--batches N` | 1 | Number of seeds to run. `0` means until stopped. |
| `--out` | `~/crush_test/maps` | Where scenario folders, `runner_log.csv` and `run_batch.log` go. |
| `--log-root` | each run's folder | Where the simulator writes its CSV files. |
| `--db` | `/mnt/ssd_1tb/crush/crush.duckdb` | The database file. Its folder must exist. |
| `--n` | 4 | Maps per family per seed. |
| `--runs-per-map` | 3 | Different crowds per map. |
| `--families` | all five | Any of `corridor funnel tjunction crossroads tree`. |
| `--start-seed` | 0 | First seed. The same seed always makes the same maps and crowds. |
| `--sim-minutes` | 60 | Simulated minutes per run. |
| `--timeout` | 3600 | Real seconds before a stuck run is killed. |
| `--jobs` | 1 | Simulations to run at the same time. |
| `--progress-every` | 30 | Seconds between progress lines for running simulations. `0` turns them off. |
| `--drop-agent-log` | off | Also delete the per-person log (`agents.csv`), which isn't stored in the database. |
| `--max-fail-streak` | 10 | Stop after this many failures in a row. |
| `--crowdwalk` | `crowdwalk/` | The folder with `quickstart.sh`. |
| `--log`, `--stop`, `--foreground` | | See *Background options* below. Default log: `<out>/run_batch.log`. |

### `force_model.py`

`python3 Crush3/force_model.py DATABASE [options]`. The database is the DuckDB file filled by `run_batch.py` (or a CSV with the same columns).

| Option | Default | Meaning |
|---|---|---|
| `--horizon` | 10 | Length of the window in ticks: the model predicts the highest value at any point in the next `--horizon` ticks. |
| `--cv K` | | K-fold test, split by run. |
| `--holdout-map MAP` | | Test on one map (its name or `map_id`), or `all` to test each map in turn. Cannot be combined with `--cv`. |
| `--test-frac` | 0.2 | With neither `--cv` nor `--holdout-map`: the share of runs held out, once. |
| `--target` | `pressure held` | What to predict: pressure (N), held time above 1112 N (s), or both. |
| `--models` | `main` | `main` (Random Forest and XGBoost with history), `other` (the comparison models) or `all`. The baselines are always included. |
| `--observable-only` | off | Hide simulated forces from the models. |
| `--top-features K` | all | Train on only the K strongest features. Needs `--features-from`. |
| `--features-from CSV` | | The `importance.csv` of an earlier run, strongest feature first. |
| `--train-runs N` | all | Train each fold on only N of its training runs, with the same test runs. |
| `--threshold` | 1062 | Pressure (N) that counts as a crossing in `crossings.csv`. |
| `--no-importance` | off | Skip the importance check, which is the slow part. |
| `--at-horizon` | off | Predict the value at exactly `--horizon` ticks ahead (the old target), not the highest value in the window. |
| `--out-dir` | `force_model_out` | Name of the results folder (dated, in the results folder), one subfolder per target. |
| `--log`, `--stop`, `--foreground` | | See *Background options* below. Default log: `<out-dir>/run.log`. |

### `sweep.py`

`python3 Crush3/sweep.py DATABASE [options]`. Pressure only, always with the highest-in-the-window target.

| Option | Default | Meaning |
|---|---|---|
| `--windows` | 30,60,120,300,600 | Window lengths to try, in ticks. |
| `--runs` | 8,16,24,32,0 | Training sizes, in runs per fold. 0 means all runs. |
| `--models` | `main` | Same as in `force_model.py`. |
| `--cv K` | 5 | K-fold test, split by run. |
| `--threshold` | 1062 | Pressure (N) that counts as a crossing. |
| `--out-dir` | `fm_sweep` | Name of the results folder (dated, in the results folder) for `results.csv`, the graphs and each run's own folder (`h<window>_runs<N>/`). |
| `--replot RESULTS_CSV` | | Only redraw `sweep.py`'s own graphs from an existing `results.csv`. |
| `--metric` | `warned_share` | What `--replot` draws: `warned_share`, `caught_share` or `alarms_real_share`. |
| `--log`, `--stop`, `--foreground` | | See *Background options* below. Default log: `<out-dir>/run.log`. |

`sweep.py` also draws its own `sweep_<model>.png` (share of events warned). On the datasets so far that share is near 100% for every window, so its lines overlap. Use `sweep_plot.py` instead.

### `history_sweep.py`

| Option | Default | Meaning |
|---|---|---|
| `data` | (required) | The DuckDB file or CSV. |
| `--back` | `0,10,30,60,120,300` | Look-backs to try, in ticks. 0 = no history. Each one includes the shorter ones. |
| `--single` | off | Each look-back gets only its own change, not the shorter ones as well. |
| `--search` | off | `forward`, `backward` or `both`: try sets of the `--back` look-backs together. Results go to `results.csv` and `<model>_<set>/` (e.g. `RF_history_30+60/`). |
| `--models` | both | With `--search`: only these models, comma-separated, so the two can run as separate parts. |
| `--window` | `120` | Window length in ticks the model predicts over. |
| `--cv` | `5` | Number of test folds, split by run. |
| `--threshold` | `1062` | Pressure (N) that counts as a crossing. |
| `--out-dir` | `fm_history` | Name of the results folder (dated, in the results folder) for `results.csv` and each look-back's own folder (`back<N>/`). |

It also takes the background options below.

### `sweep_plot.py`

`python3 Crush3/sweep_plot.py RESULTS_CSV [--models A,B]`. Reads a `results.csv` from `sweep.py` or `error_bars.py`, prints the numbers, and writes the pictures next to the csv.

| Option | Default | Meaning |
|---|---|---|
| `--models` | every model in the csv | Only these models, comma-separated. |

### `error_bars.py`

`python3 Crush3/error_bars.py DATABASE FOLDER [FOLDER ...] [options]`. Each folder is a `sweep.py` out-dir. It reads the saved predictions of every window and training size in it (`h<window>_runs<N>/predictions.csv`), so the data-size graphs get a range at every point too. The range covers which runs happened to be tested. It does not cover which training runs were picked for a smaller training size.

| Option | Default | Meaning |
|---|---|---|
| `--all-runs-only` | off | Only the results trained on all runs (faster; enough for the bar graphs). |
| `--threshold` | 1062 | Pressure (N) that counts as a crossing. Must match the sweep. |
| `--out` | `fm_errorbars/results.csv` | The table it writes, with `_lo` and `_hi` columns for each number. The folder part is dated and put in the results folder like an `--out-dir`. |

It first recomputes each number and compares it with what the sweep saved, and stops with a message if they differ (wrong database or threshold).

### Background options

For `run_batch.py`, `force_model.py` and `sweep.py` (section 2).

| Option | Meaning |
|---|---|
| `--log FILE` | The log file of the background job. |
| `--stop --log FILE` | End the job that writes to FILE, then exit. No other argument is needed. |
| `--foreground` | Run in this terminal: no log file, Ctrl-C ends the job. |

### `make_maps.py` and `link_context.py`

You normally don't run these yourself. If you want to:

```
# make scenarios without running them
python3 Crush3/make_maps.py --n 10 --runs-per-map 3 --seed 1 --out generated_maps

# load one finished run into the database by hand
python3 Crush3/link_context.py path/to/linkMetrics.csv path/to/prop.json --db crush.duckdb --run-id my_run
```

`make_maps.py` takes `--n`, `--runs-per-map`, `--seed`, `--families`, `--sim-minutes`, `--out`, `--log-root`, and `--template` (the folder whose `prop.json`, `gen.json` and agent scripts every scenario copies; this folder by default).
