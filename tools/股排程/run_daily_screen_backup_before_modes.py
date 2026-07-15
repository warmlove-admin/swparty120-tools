import argparse
import csv
import json
import os
import re
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime, time as dtime
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
CODE_RE = re.compile(r"^[1-9][0-9]{3}$")


@dataclass
class ScreenRow:
    code: str
    name: str
    exchange: str
    level: str
    score: float
    close: float
    open: float
    high: float
    low: float
    reference: float
    return_pct: float
    gap_pct: float
    fade_pct: float
    spread_pct: float
    volume: int
    total_amount: float
    liquidity_score: int
    opening_score: int
    momentum_score: int
    technical_score: int
    risk_score: int
    reasons: str


def env_required(name):
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def to_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def to_exchange(value):
    text = str(value)
    if "." in text:
        text = text.rsplit(".", 1)[-1]
    return text.replace("Exchange.", "")


def load_api():
    load_dotenv(BASE_DIR / ".env", encoding="utf-8-sig")
    import shioaji as sj

    simulation = os.getenv("SHIOAJI_SIMULATION", "true").lower() != "false"
    api = sj.Shioaji(simulation=simulation)
    api.login(
        api_key=env_required("SHIOAJI_API_KEY"),
        secret_key=env_required("SHIOAJI_SECRET_KEY"),
    )
    return api, simulation


def iter_stock_contracts(api):
    for group_name in ("TSE", "OTC"):
        group = getattr(api.Contracts.Stocks, group_name)
        for contract in group:
            code = getattr(contract, "code", "")
            if CODE_RE.match(code):
                yield contract


def chunks(items, size):
    for index in range(0, len(items), size):
        yield items[index : index + size]


def snapshot_to_dict(snapshot, contract):
    return {
        "code": getattr(contract, "code", ""),
        "name": getattr(contract, "name", ""),
        "exchange": to_exchange(getattr(contract, "exchange", "")),
        "day_trade": str(getattr(contract, "day_trade", "")),
        "category": getattr(contract, "category", ""),
        "reference": to_float(getattr(contract, "reference", 0)),
        "limit_up": to_float(getattr(contract, "limit_up", 0)),
        "limit_down": to_float(getattr(contract, "limit_down", 0)),
        "open": to_float(getattr(snapshot, "open", 0)),
        "high": to_float(getattr(snapshot, "high", 0)),
        "low": to_float(getattr(snapshot, "low", 0)),
        "close": to_float(getattr(snapshot, "close", 0)),
        "buy_price": to_float(getattr(snapshot, "buy_price", 0)),
        "sell_price": to_float(getattr(snapshot, "sell_price", 0)),
        "volume": int(to_float(getattr(snapshot, "total_volume", 0))),
        "total_amount": to_float(getattr(snapshot, "total_amount", 0)),
        "ts": datetime.now().isoformat(timespec="seconds"),
    }


def fetch_snapshots(api):
    contracts = list(iter_stock_contracts(api))
    by_code = {contract.code: contract for contract in contracts}
    rows = []
    for batch in chunks(contracts, 200):
        try:
            snapshots = api.snapshots(batch)
        except Exception as exc:
            print(f"snapshot batch failed: {exc}", file=sys.stderr)
            continue
        for snapshot in snapshots:
            contract = by_code.get(str(getattr(snapshot, "code", "")))
            if contract:
                rows.append(snapshot_to_dict(snapshot, contract))
        time.sleep(0.1)
    return rows


def hard_exclusions(row):
    reasons = []
    close = row["close"]
    reference = row["reference"]
    buy_price = row["buy_price"]
    sell_price = row["sell_price"]
    high = row["high"]
    low = row["low"]

    if "Yes" not in row["day_trade"]:
        reasons.append("不可當沖")
    if close <= 0 or reference <= 0:
        reasons.append("無有效行情")
    if row["volume"] < 1000:
        reasons.append("成交量<1000張")
    if close > 0 and buy_price > 0 and sell_price > 0:
        spread = (sell_price - buy_price) / close * 100
        if spread > 0.6:
            reasons.append("五檔價差>0.6%")
    else:
        reasons.append("無有效買賣價")
    if close > 0 and row["limit_up"] > 0 and (row["limit_up"] - close) / close * 100 < 1.5:
        reasons.append("距漲停<1.5%")
    if close > 0 and row["limit_down"] > 0 and (close - row["limit_down"]) / close * 100 < 2:
        reasons.append("距跌停<2%")
    if reference > 0 and high > low and (high - low) / reference * 100 > 8 and close < row["open"]:
        reasons.append("振幅>8%且低於開盤")

    return reasons


def score_row(row):
    close = row["close"]
    open_price = row["open"]
    high = row["high"]
    low = row["low"]
    reference = row["reference"]
    buy_price = row["buy_price"]
    sell_price = row["sell_price"]
    volume = row["volume"]

    return_pct = (close - reference) / reference * 100 if reference else 0
    gap_pct = (open_price - reference) / reference * 100 if reference else 0
    fade_pct = (close - open_price) / open_price * 100 if open_price else 0
    spread_pct = (sell_price - buy_price) / close * 100 if close and buy_price and sell_price else 999
    range_pct = (high - low) / reference * 100 if reference else 0

    reasons = []
    liquidity = 0
    liquidity += 8 if volume >= 10000 else 6 if volume >= 5000 else 4 if volume >= 2000 else 2
    liquidity += 6 if row["total_amount"] >= 500_000_000 else 4 if row["total_amount"] >= 150_000_000 else 2
    liquidity += 4 if spread_pct <= 0.2 else 3 if spread_pct <= 0.4 else 1

    opening = 0
    if close > open_price:
        opening += 5
        reasons.append("現價高於開盤")
    elif close == open_price:
        opening += 3
    position_from_high = (high - close) / (high - low) if high > low else 0
    if position_from_high <= 0.25:
        opening += 4
        reasons.append("接近日高")
    elif position_from_high <= 0.5:
        opening += 2
    opening += 4 if return_pct >= 2 else 2 if return_pct >= 1 else 1 if return_pct > 0 else 0
    if high >= reference * 1.03:
        opening += 3
    opening += 4 if fade_pct >= 0.5 else 2 if fade_pct >= 0 else -5 if fade_pct <= -1 else 0
    opening += 5 if volume >= 20000 else 4 if volume >= 10000 else 3 if volume >= 5000 else 1
    opening = max(0, min(25, opening))

    momentum = 0
    momentum += 5 if gap_pct >= 2 else 3 if gap_pct >= 1 else 1 if gap_pct > 0 else 0
    momentum += 5 if return_pct >= 4 else 4 if return_pct >= 3 else 3 if return_pct >= 2 else 1 if return_pct > 0 else 0
    if gap_pct > 0 and fade_pct >= 0 and return_pct > 1:
        momentum += 3
    if return_pct >= 4 and volume >= 3000:
        reasons.append("強勢動能")
    momentum = min(15, momentum)

    technical = 0
    if close > reference:
        technical += 3
    if close > open_price:
        technical += 2
    if 2 <= range_pct <= 6:
        technical += 2
    if fade_pct >= 0:
        technical += 2
    if spread_pct <= 0.2:
        technical += 1
    technical = min(10, technical)

    risk_score = 5
    if range_pct > 6:
        risk_score -= 2
    if spread_pct > 0.4:
        risk_score -= 2
    risk_score = max(0, risk_score)

    raw = liquidity + opening + momentum + technical + risk_score
    score = round(raw / 73 * 100, 1)
    level = "A" if score >= 80 and liquidity >= 15 and opening >= 18 else "B" if score >= 70 else "C"

    return ScreenRow(
        code=row["code"],
        name=row["name"],
        exchange=row["exchange"],
        level=level,
        score=score,
        close=round(close, 4),
        open=round(open_price, 4),
        high=round(high, 4),
        low=round(low, 4),
        reference=round(reference, 4),
        return_pct=round(return_pct, 2),
        gap_pct=round(gap_pct, 2),
        fade_pct=round(fade_pct, 2),
        spread_pct=round(spread_pct, 3),
        volume=volume,
        total_amount=round(row["total_amount"], 0),
        liquidity_score=liquidity,
        opening_score=opening,
        momentum_score=momentum,
        technical_score=technical,
        risk_score=risk_score,
        reasons="、".join(reasons[:5]),
    )


def write_csv(path, rows, fieldnames):
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_report(path, run_time, simulation, candidates, excluded_count):
    a_rows = [row for row in candidates if row.level == "A"]
    lines = [
        f"# Stock Screen Report {run_time:%Y-%m-%d %H:%M:%S}",
        "",
        f"- Simulation: {simulation}",
        f"- A candidates: {len(a_rows)}",
        f"- Total candidates: {len(candidates)}",
        f"- Excluded: {excluded_count}",
        "",
        "## A Candidates",
        "",
        "| Rank | Code | Name | Score | Return | Volume | Reasons |",
        "|---:|---|---|---:|---:|---:|---|",
    ]
    for index, row in enumerate(a_rows[:30], start=1):
        lines.append(
            f"| {index} | {row.code} | {row.name} | {row.score:.1f} | "
            f"{row.return_pct:.2f}% | {row.volume} | {row.reasons} |"
        )
    if not a_rows:
        lines.append("| - | - | No A candidates | - | - | - | - |")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def run_once():
    run_time = datetime.now()
    output_dir = DATA_DIR / run_time.strftime("%Y-%m-%d")
    output_dir.mkdir(parents=True, exist_ok=True)

    api, simulation = load_api()
    try:
        snapshot_rows = fetch_snapshots(api)
    finally:
        api.logout()

    write_csv(
        output_dir / "snapshots.csv",
        snapshot_rows,
        [
            "ts",
            "code",
            "name",
            "exchange",
            "day_trade",
            "category",
            "reference",
            "limit_up",
            "limit_down",
            "open",
            "high",
            "low",
            "close",
            "buy_price",
            "sell_price",
            "volume",
            "total_amount",
        ],
    )

    candidates = []
    excluded = []
    for row in snapshot_rows:
        reasons = hard_exclusions(row)
        if reasons:
            excluded.append({**row, "exclude_reasons": "、".join(reasons)})
            continue
        scored = score_row(row)
        if scored.level in ("A", "B"):
            candidates.append(scored)

    candidates.sort(key=lambda item: (item.level, -item.score))
    candidates.sort(key=lambda item: item.score, reverse=True)

    candidate_dicts = [asdict(row) for row in candidates]
    write_csv(output_dir / "candidates.csv", candidate_dicts, list(candidate_dicts[0].keys()) if candidate_dicts else list(ScreenRow.__annotations__.keys()))
    (output_dir / "candidates.json").write_text(
        json.dumps(candidate_dicts, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if excluded:
        write_csv(output_dir / "excluded.csv", excluded, list(excluded[0].keys()))
    else:
        write_csv(output_dir / "excluded.csv", [], ["code", "name", "exclude_reasons"])
    write_report(output_dir / "screening_report.md", run_time, simulation, candidates, len(excluded))

    print(f"output_dir={output_dir}")
    print(f"snapshots={len(snapshot_rows)} candidates={len(candidates)} excluded={len(excluded)}")
    for row in candidates[:10]:
        print(f"{row.level} {row.code} {row.name} score={row.score:.1f} return={row.return_pct:.2f}% volume={row.volume}")


def run_window():
    today = datetime.now().date()
    start = datetime.combine(today, dtime(8, 30))
    end = datetime.combine(today, dtime(9, 10))
    now = datetime.now()
    if now < start:
        time.sleep((start - now).total_seconds())
    while datetime.now() < end:
        interval = 30 if datetime.now().time() < dtime(9, 0) else 5
        run_once()
        time.sleep(interval)
    run_once()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Run one current-time screen.")
    parser.add_argument("--window", action="store_true", help="Run 8:30-9:10 window collection.")
    args = parser.parse_args()
    if args.window:
        run_window()
    else:
        run_once()


if __name__ == "__main__":
    main()
