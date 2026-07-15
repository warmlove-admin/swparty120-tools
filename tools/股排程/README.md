# Stock Screener

This is a read-only SinoPac Shioaji screener for day-trading, overnight, and
weekly watchlist workflows.

It logs in to Shioaji, collects stock snapshots, applies hard risk exclusions,
scores candidates, and writes daily output files. It does not place orders and
does not activate CA.

## Files

- `.env`: local API credentials and optional screener settings. Do not share it.
- `run_daily_screen.py`: read-only screener.
- `run_once.ps1`: manual run helper.
- `run_window.ps1`: 8:30-9:10 collection helper for Windows Task Scheduler.
- `data/YYYY-MM-DD/`: daily results.

## Output

Each run writes to:

```text
data\YYYY-MM-DD\
```

Important files:

- `snapshots.csv`: raw snapshot rows used by the screener.
- `daytrade_candidates.csv`: candidates for same-day trading.
- `overnight_candidates.csv`: candidates for next-day trading.
- `weekly_candidates.csv`: candidates for weekly swing trading.
- `candidates.csv`: same as `daytrade_candidates.csv`, kept for compatibility.
- `screening_report.md`: human-readable summary.
- `excluded.csv`: excluded stocks with reasons.

## Manual Run

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\run_once.ps1"
```

## Current Risk Defaults

These are defaults inside `run_daily_screen.py`. You can override them by adding
the same variable names to `.env`.

```env
STOCK_MAX_PRICE=200
STOCK_MAX_LOT_AMOUNT=200000
STOCK_MIN_VOLUME=1000
STOCK_MAX_SPREAD_PCT=0.6
STOCK_COMMISSION_RATE=0.001425
STOCK_COMMISSION_DISCOUNT=0.6
STOCK_MIN_COMMISSION=20
STOCK_INTRADAY_TAX_RATE=0.0015
STOCK_REGULAR_TAX_RATE=0.003
STOCK_MIN_RR_RATIO=2.0

DAYTRADE_TARGET_PCT=2.5
DAYTRADE_STOP_PCT=0.6
DAYTRADE_MIN_NET_PROFIT=800

OVERNIGHT_TARGET_PCT=5.0
OVERNIGHT_STOP_PCT=1.5
OVERNIGHT_MIN_NET_PROFIT=1500

WEEKLY_TARGET_PCT=8.0
WEEKLY_STOP_PCT=3.0
WEEKLY_MIN_NET_PROFIT=2500
```

The target/stop values are not orders. They are used to decide whether a stock
has enough price room after fees and tax.

## Scheduled Run

Windows Task Scheduler task:

```text
StockScreenDaily
```

Current schedule:

```text
08:25 daily
```

The scheduled task runs:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Users\USER\1.Claude Code\swparty120-tools\tools\股排程\run_window.ps1"
```

Window behavior:

```text
08:30-09:00 collect snapshots every 30 seconds
09:00-09:10 collect snapshots every 5 seconds
09:10 write report
```

Manual runs outside that window still work, but the result should be treated as
the current-time screen, not the official 9:10 screen.

## Recent Hot Themes

Recent theme scoring is configured in:

```text
themes_recent.json
```

The screener reloads this file on every run. A hot theme only adds score and ranking priority; it does not bypass risk filters, price limits, volume requirements, technical conditions, or trade-plan checks.

Each theme can use:

```json
{
  "label": "近期熱門-AI電力/重電/電網",
  "hot": true,
  "bonus": 8,
  "codes": ["1504", "1513", "1514"]
}
```

Recommended `bonus` range is 3-8. Use a higher number only when the theme is clearly active and has same-theme breadth.
## Corporate Action Filter

The screener automatically checks public TWSE and TPEx ex-right/ex-dividend preview data on every run. The lookup uses the current run date, so it is not tied to a single calendar year.

Default exclusion windows:

```text
pre_breakout: before 3 days, after 5 days
daytrade: before 1 day, after 2 days
overnight: before 2 days, after 5 days
weekly: before 2 days, after 5 days
```

`corporate_actions_watchlist.json` is only for manual special cases such as capital reduction, disposition stocks, material events, or names you want to avoid manually.