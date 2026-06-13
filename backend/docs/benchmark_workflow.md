# Real-log benchmark workflow

Use this workflow to measure detection quality after algorithm changes, using logs you trust plus labelled expectations.

## What already exists

| Piece | Purpose |
|-------|---------|
| `benchmark_logs.json` | Simple “run brain on these files” config (no ground truth). |
| `benchmark_runner.py` | Extracts events and prints top alerts per log. |
| `benchmark_expectations.yaml` | **Labelled expectations** — what should / should not be detected. |
| `benchmark_expectations.py` | Compares brain output to expectations. |
| `run_benchmark_analysis.py` | CLI report. |
| `tests/test_confidence_benchmarks.py` | Synthetic jitter/noise scenarios (no real files). |
| `tests/test_benchmark_expectations.py` | Optional pytest hook for your YAML file. |

Synthetic tests catch regressions in scoring math. **Real-log benchmarks** catch regressions in what you actually care about on production-shaped data.

## One-time setup

```bash
cd backend
pip install -r requirements.txt
```

Copy the example config:

```bash
cp benchmark_expectations.yaml.example benchmark_expectations.yaml
```

Edit `benchmark_expectations.yaml`:

1. Set `enabled: true` on a benchmark block.
2. Point `path` at your log file (absolute path recommended on Windows).
3. Set `log_id` to match the parser (`0` = Windows Security, `1` = Sysmon — see `log_registry.py`).
4. Add `expectations` for patterns you know are true or false.

### Expectation fields

| Field | Meaning |
|-------|---------|
| `label` | Human-readable name shown in reports. |
| `native_event_id` | Optional filter to one event type. |
| `period_ms` | Expected period in milliseconds (`300000` = 5 min). |
| `period_tolerance_pct` | Allowed period drift (default 5%). |
| `min_confidence` | For `must_detect: true` — fail if best match is below this. |
| `max_confidence` | Upper bound (for ghosts you expect to stay weak). |
| `min_windows` | Minimum merged window count. |
| `must_detect` | `true` = must find a matching alert; `false` = should be absent or below `max_confidence`. |
| `notes` | Free text for future you / the agent. |

## Running analysis

### CLI report

```bash
python run_benchmark_analysis.py
```

Example output:

```
## nightly_backup_security
- [PASS] true 5-minute backup cycle: Detected period=300000ms conf=72 windows=3
- [PASS] 1-minute subharmonic ghost should stay weak: Ghost/low alert present but acceptable: period=60000ms conf=38

Summary: pass=2 missing=0 weak=0 false_positive=0
```

### Verdict meanings

| Status | Meaning |
|--------|---------|
| `PASS` | Behaviour matches expectation. |
| `MISSING` | `must_detect: true` but no alert matched period/event filters. |
| `WEAK` | Alert found but confidence/windows below your thresholds. |
| `FALSE_POSITIVE` | `must_detect: false` but a matching alert exceeded `max_confidence`. |

### Pytest (optional local check)

```bash
pytest tests/test_benchmark_expectations.py -m slow
```

This is **optional** — it only runs when you have created `benchmark_expectations.yaml` with `enabled: true` entries. It is not part of normal CI unless you wire it up yourself. The `-m slow` flag means “only tests marked as slow” (real log files, not synthetic math tests).

## How to interpret results after a code change

1. **Run before and after** the change on the same YAML file.
2. For each expectation, note:
   - **True positive** — `PASS` on `must_detect: true`.
   - **Acceptable ghost** — `PASS` on `must_detect: false` with low confidence.
   - **Regression** — `MISSING` / `WEAK` on something you know is real.
   - **New false positive** — `FALSE_POSITIVE` on something that should stay quiet.
3. If a true alarm disappeared, check:
   - Was it single-window? (confidence cap may apply.)
   - Is period much shorter than median spacing? (spacing alias penalty, not hard drop.)
   - Did subharmonic stepping change? (disabled by default since 2025-06 — prefer penalty over period rewrite.)
4. Record conclusions in the expectation `notes` field so the next tuning session has context.

## Adding a new log benchmark

1. Place or reference the log file (JSON export from your pipeline).
2. Manually inspect timestamps for one `native_event_id` you understand.
3. Add a benchmark block with at least one `must_detect: true` expectation.
4. Add one or more `must_detect: false` expectations for known ghost periods (e.g. 1 min when truth is 5 min).
5. Run `python run_benchmark_analysis.py` and iterate on algorithm/config until verdicts match your judgement.
6. Optionally tighten `min_confidence` once stable.

## Files to commit vs keep local

- **Commit:** `benchmark_expectations.yaml.example`, docs, code.
- **Keep local (gitignored):** `benchmark_expectations.yaml` with real paths — add to `.gitignore` if it contains sensitive paths.

## Questions for the log owner

When creating expectations, capture:

- Which `native_event_id`(s) are in scope?
- Exact or approximate period(s) in human units?
- Is the signal intermittent (few windows) or sustained?
- Known false-positive periods to watch?
- Minimum confidence you would act on in the UI?
