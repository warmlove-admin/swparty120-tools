import argparse
import csv
import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, time as dtime
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
CODE_RE = re.compile(r"^[1-9][0-9]{3}$")


@dataclass
class Settings:
    max_price: float = 200.0
    max_lot_amount: float = 200_000.0
    min_volume: int = 1000
    max_spread_pct: float = 0.6
    commission_rate: float = 0.001425
    commission_discount: float = 0.6
    min_commission: float = 20.0
    intraday_tax_rate: float = 0.0015
    regular_tax_rate: float = 0.003
    daytrade_target_pct: float = 2.5
    daytrade_stop_pct: float = 0.6
    daytrade_min_net_profit: float = 800.0
    overnight_target_pct: float = 5.0
    overnight_stop_pct: float = 1.5
    overnight_min_net_profit: float = 1500.0
    weekly_target_pct: float = 8.0
    weekly_stop_pct: float = 3.0
    weekly_min_net_profit: float = 2500.0
    min_rr_ratio: float = 2.0
    history_pool_size: int = 160
    kbar_days: int = 70


@dataclass
class Candidate:
    mode: str
    code: str
    name: str
    exchange: str
    level: str
    score: float
    entry_price: float
    target_price: float
    stop_price: float
    lot_amount: float
    net_profit_at_target: float
    loss_at_stop: float
    rr_ratio: float
    return_pct: float
    gap_pct: float
    fade_pct: float
    spread_pct: float
    volume: int
    total_amount: float
    liquidity_score: int
    momentum_score: int
    technical_score: int
    risk_score: int
    reasons: str


def env_float(name, default):
    value = os.getenv(name)
    if value in (None, ""):
        return default
    return float(value)


def env_int(name, default):
    value = os.getenv(name)
    if value in (None, ""):
        return default
    return int(value)


def load_settings():
    return Settings(
        max_price=env_float("STOCK_MAX_PRICE", Settings.max_price),
        max_lot_amount=env_float("STOCK_MAX_LOT_AMOUNT", Settings.max_lot_amount),
        min_volume=env_int("STOCK_MIN_VOLUME", Settings.min_volume),
        max_spread_pct=env_float("STOCK_MAX_SPREAD_PCT", Settings.max_spread_pct),
        commission_rate=env_float("STOCK_COMMISSION_RATE", Settings.commission_rate),
        commission_discount=env_float("STOCK_COMMISSION_DISCOUNT", Settings.commission_discount),
        min_commission=env_float("STOCK_MIN_COMMISSION", Settings.min_commission),
        intraday_tax_rate=env_float("STOCK_INTRADAY_TAX_RATE", Settings.intraday_tax_rate),
        regular_tax_rate=env_float("STOCK_REGULAR_TAX_RATE", Settings.regular_tax_rate),
        daytrade_target_pct=env_float("DAYTRADE_TARGET_PCT", Settings.daytrade_target_pct),
        daytrade_stop_pct=env_float("DAYTRADE_STOP_PCT", Settings.daytrade_stop_pct),
        daytrade_min_net_profit=env_float("DAYTRADE_MIN_NET_PROFIT", Settings.daytrade_min_net_profit),
        overnight_target_pct=env_float("OVERNIGHT_TARGET_PCT", Settings.overnight_target_pct),
        overnight_stop_pct=env_float("OVERNIGHT_STOP_PCT", Settings.overnight_stop_pct),
        overnight_min_net_profit=env_float("OVERNIGHT_MIN_NET_PROFIT", Settings.overnight_min_net_profit),
        weekly_target_pct=env_float("WEEKLY_TARGET_PCT", Settings.weekly_target_pct),
        weekly_stop_pct=env_float("WEEKLY_STOP_PCT", Settings.weekly_stop_pct),
        weekly_min_net_profit=env_float("WEEKLY_MIN_NET_PROFIT", Settings.weekly_min_net_profit),
        min_rr_ratio=env_float("STOCK_MIN_RR_RATIO", Settings.min_rr_ratio),
        history_pool_size=env_int("STOCK_HISTORY_POOL_SIZE", Settings.history_pool_size),
        kbar_days=env_int("STOCK_KBAR_DAYS", Settings.kbar_days),
    )


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
    api.login(api_key=env_required("SHIOAJI_API_KEY"), secret_key=env_required("SHIOAJI_SECRET_KEY"))
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
    return rows, by_code


def fee(amount, settings):
    return max(settings.min_commission, amount * settings.commission_rate * settings.commission_discount)


def trade_math(entry, target, stop, mode, settings):
    shares = 1000
    tax_rate = settings.intraday_tax_rate if mode == "daytrade" else settings.regular_tax_rate
    buy_amount = entry * shares
    target_amount = target * shares
    stop_amount = stop * shares

    buy_fee = fee(buy_amount, settings)
    sell_fee_target = fee(target_amount, settings)
    sell_fee_stop = fee(stop_amount, settings)
    tax_target = target_amount * tax_rate
    tax_stop = stop_amount * tax_rate

    net_profit = target_amount - buy_amount - buy_fee - sell_fee_target - tax_target
    stop_loss = buy_amount - stop_amount + buy_fee + sell_fee_stop + tax_stop
    rr = net_profit / stop_loss if stop_loss > 0 else 0
    return round(net_profit, 0), round(stop_loss, 0), round(rr, 2)


def common_exclusions(row, settings):
    reasons = []
    close = row["close"]
    reference = row["reference"]
    buy_price = row["buy_price"]
    sell_price = row["sell_price"]
    spread_pct = (sell_price - buy_price) / close * 100 if close and buy_price and sell_price else 999

    if close <= 0 or reference <= 0:
        reasons.append("invalid_quote")
    if row["volume"] < settings.min_volume:
        reasons.append("volume_too_low")
    if spread_pct > settings.max_spread_pct:
        reasons.append("spread_too_wide")
    if close > settings.max_price:
        reasons.append("price_above_limit")
    if close * 1000 > settings.max_lot_amount:
        reasons.append("one_lot_amount_above_limit")
    return reasons


def mode_trade_plan(row, mode, settings):
    entry = row["close"]
    if mode == "daytrade":
        target_pct = settings.daytrade_target_pct
        stop_pct = settings.daytrade_stop_pct
        min_net = settings.daytrade_min_net_profit
    elif mode == "overnight":
        target_pct = settings.overnight_target_pct
        stop_pct = settings.overnight_stop_pct
        min_net = settings.overnight_min_net_profit
    else:
        target_pct = settings.weekly_target_pct
        stop_pct = settings.weekly_stop_pct
        min_net = settings.weekly_min_net_profit

    target = round(entry * (1 + target_pct / 100), 2)
    stop = round(entry * (1 - stop_pct / 100), 2)
    net_profit, stop_loss, rr = trade_math(entry, target, stop, mode, settings)
    reasons = []
    if net_profit < min_net:
        reasons.append("net_profit_below_min")
    if rr < settings.min_rr_ratio:
        reasons.append("rr_below_min")
    return target, stop, net_profit, stop_loss, rr, reasons


def snapshot_base_scores(row):
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

    liquidity = 0
    liquidity += 8 if volume >= 10000 else 6 if volume >= 5000 else 4 if volume >= 2000 else 2
    liquidity += 6 if row["total_amount"] >= 500_000_000 else 4 if row["total_amount"] >= 150_000_000 else 2
    liquidity += 4 if spread_pct <= 0.2 else 3 if spread_pct <= 0.4 else 1

    momentum = 0
    momentum += 5 if gap_pct >= 2 else 3 if gap_pct >= 1 else 1 if gap_pct > 0 else 0
    momentum += 5 if return_pct >= 4 else 4 if return_pct >= 3 else 3 if return_pct >= 2 else 1 if return_pct > 0 else 0
    if gap_pct > 0 and fade_pct >= 0 and return_pct > 1:
        momentum += 3

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

    risk = 5
    if range_pct > 6:
        risk -= 2
    if spread_pct > 0.4:
        risk -= 2

    reasons = []
    if close > open_price:
        reasons.append("above_open")
    if high > low and (high - close) / (high - low) <= 0.25:
        reasons.append("near_day_high")
    if return_pct >= 4 and volume >= 3000:
        reasons.append("strong_momentum")

    return {
        "return_pct": return_pct,
        "gap_pct": gap_pct,
        "fade_pct": fade_pct,
        "spread_pct": spread_pct,
        "liquidity": liquidity,
        "momentum": min(15, momentum),
        "technical": min(10, technical),
        "risk": max(0, risk),
        "reasons": reasons,
    }


def make_candidate(row, mode, settings, history=None):
    if mode == "daytrade" and "Yes" not in row["day_trade"]:
        return None, ["not_day_trade"]

    reasons = common_exclusions(row, settings)
    target, stop, net_profit, stop_loss, rr, trade_reasons = mode_trade_plan(row, mode, settings)
    reasons.extend(trade_reasons)
    if reasons:
        return None, reasons

    scores = snapshot_base_scores(row)
    liquidity = scores["liquidity"]
    momentum = scores["momentum"]
    technical = scores["technical"]
    risk = scores["risk"]
    reason_tags = list(scores["reasons"])

    if mode == "overnight" and history:
        add, tags = overnight_history_score(history)
        technical += add
        reason_tags.extend(tags)
    elif mode == "weekly" and history:
        add, tags = weekly_history_score(history)
        technical += add
        reason_tags.extend(tags)

    max_raw = 73 if mode == "daytrade" else 80
    raw = liquidity + momentum + min(20, technical) + risk
    if mode == "daytrade":
        raw += 15 if row["close"] > row["open"] else 5
    else:
        raw += 10 if scores["return_pct"] > 0 else 0

    score = round(raw / max_raw * 100, 1)
    level = "A" if score >= 80 and liquidity >= 14 and rr >= settings.min_rr_ratio else "B" if score >= 70 else "C"
    if level == "C":
        return None, ["score_below_b"]

    return Candidate(
        mode=mode,
        code=row["code"],
        name=row["name"],
        exchange=row["exchange"],
        level=level,
        score=score,
        entry_price=round(row["close"], 4),
        target_price=target,
        stop_price=stop,
        lot_amount=round(row["close"] * 1000, 0),
        net_profit_at_target=net_profit,
        loss_at_stop=stop_loss,
        rr_ratio=rr,
        return_pct=round(scores["return_pct"], 2),
        gap_pct=round(scores["gap_pct"], 2),
        fade_pct=round(scores["fade_pct"], 2),
        spread_pct=round(scores["spread_pct"], 3),
        volume=row["volume"],
        total_amount=round(row["total_amount"], 0),
        liquidity_score=liquidity,
        momentum_score=momentum,
        technical_score=min(20, technical),
        risk_score=risk,
        reasons=",".join(reason_tags[:8]),
    ), []


def daily_bars_from_kbars(kbars):
    grouped = {}
    for ts, open_, high, low, close, volume in zip(
        kbars.ts, kbars.Open, kbars.High, kbars.Low, kbars.Close, kbars.Volume
    ):
        day = datetime.fromtimestamp(ts / 1_000_000_000).date().isoformat()
        bucket = grouped.setdefault(
            day,
            {"date": day, "open": open_, "high": high, "low": low, "close": close, "volume": 0},
        )
        bucket["high"] = max(bucket["high"], high)
        bucket["low"] = min(bucket["low"], low)
        bucket["close"] = close
        bucket["volume"] += volume
    return [grouped[key] for key in sorted(grouped)]


def avg(values):
    return sum(values) / len(values) if values else 0


def fetch_history_features(api, contracts, days):
    end = datetime.now().date()
    start = end - timedelta(days=days)
    features = {}
    for contract in contracts:
        try:
            kbars = api.kbars(contract, start=start.isoformat(), end=end.isoformat())
            bars = daily_bars_from_kbars(kbars)
            if len(bars) >= 10:
                features[contract.code] = bars
        except Exception as exc:
            print(f"kbar failed {contract.code}: {exc}", file=sys.stderr)
        time.sleep(0.03)
    return features


def overnight_history_score(bars):
    tags = []
    closes = [bar["close"] for bar in bars]
    vols = [bar["volume"] for bar in bars]
    last = bars[-1]
    ma5 = avg(closes[-5:])
    ma10 = avg(closes[-10:])
    vol5 = avg(vols[-5:])
    score = 0
    if last["close"] > ma5:
        score += 4
        tags.append("above_ma5")
    if last["close"] > ma10:
        score += 3
        tags.append("above_ma10")
    if ma5 > ma10:
        score += 3
        tags.append("ma5_gt_ma10")
    if vol5 and last["volume"] > vol5 * 1.3:
        score += 4
        tags.append("volume_expansion")
    if last["high"] > last["low"] and (last["high"] - last["close"]) / (last["high"] - last["low"]) <= 0.25:
        score += 3
        tags.append("daily_close_near_high")
    return score, tags


def weekly_history_score(bars):
    tags = []
    closes = [bar["close"] for bar in bars]
    vols = [bar["volume"] for bar in bars]
    last = bars[-1]
    ma5 = avg(closes[-5:])
    ma10 = avg(closes[-10:])
    ma20 = avg(closes[-20:]) if len(closes) >= 20 else ma10
    vol20 = avg(vols[-20:]) if len(vols) >= 20 else avg(vols)
    score = 0
    if last["close"] > ma5 > ma10:
        score += 5
        tags.append("short_trend_up")
    if ma5 > ma10 > ma20:
        score += 5
        tags.append("ma_alignment")
    if closes[-1] > closes[-5]:
        score += 3
        tags.append("five_day_price_up")
    if vol20 and avg(vols[-5:]) > vol20 * 1.2:
        score += 4
        tags.append("five_day_volume_up")
    if last["close"] > ma20:
        score += 3
        tags.append("above_ma20")
    return score, tags


def write_csv(path, rows, fieldnames):
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_candidates(output_dir, filename, rows):
    dicts = [asdict(row) for row in rows]
    fields = list(Candidate.__annotations__.keys())
    write_csv(output_dir / f"{filename}.csv", dicts, fields)
    (output_dir / f"{filename}.json").write_text(
        json.dumps(dicts, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def write_report(path, run_time, simulation, settings, groups, excluded_count):
    lines = [
        f"# Stock Screen Report {run_time:%Y-%m-%d %H:%M:%S}",
        "",
        f"- Simulation: {simulation}",
        f"- Max price: {settings.max_price}",
        f"- Max one-lot amount: {settings.max_lot_amount:.0f}",
        f"- Min risk/reward: {settings.min_rr_ratio}",
        f"- Excluded rows: {excluded_count}",
        "",
    ]
    labels = {
        "daytrade": "Daytrade candidates",
        "overnight": "Overnight candidates",
        "weekly": "Weekly candidates",
    }
    for mode, rows in groups.items():
        a_rows = [row for row in rows if row.level == "A"]
        lines.extend(
            [
                f"## {labels[mode]}",
                "",
                f"- A candidates: {len(a_rows)}",
                f"- Total candidates: {len(rows)}",
                "",
                "| Rank | Code | Name | Level | Score | Entry | Target | Stop | Net target | RR | Return | Volume | Reasons |",
                "|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
            ]
        )
        for index, row in enumerate(rows[:30], start=1):
            lines.append(
                f"| {index} | {row.code} | {row.name} | {row.level} | {row.score:.1f} | "
                f"{row.entry_price:.2f} | {row.target_price:.2f} | {row.stop_price:.2f} | "
                f"{row.net_profit_at_target:.0f} | {row.rr_ratio:.2f} | {row.return_pct:.2f}% | "
                f"{row.volume} | {row.reasons} |"
            )
        if not rows:
            lines.append("| - | - | No candidates | - | - | - | - | - | - | - | - | - | - |")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def rank_snapshot_pool(rows, settings):
    ranked = []
    for row in rows:
        if common_exclusions(row, settings):
            continue
        scores = snapshot_base_scores(row)
        ranked.append((scores["liquidity"] + scores["momentum"] + scores["technical"], row))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [row for _, row in ranked[: settings.history_pool_size]]


def run_once():
    run_time = datetime.now()
    output_dir = DATA_DIR / run_time.strftime("%Y-%m-%d")
    output_dir.mkdir(parents=True, exist_ok=True)

    api, simulation = load_api()
    settings = load_settings()
    try:
        snapshot_rows, contracts_by_code = fetch_snapshots(api)
        history_pool = rank_snapshot_pool(snapshot_rows, settings)
        history_contracts = [contracts_by_code[row["code"]] for row in history_pool if row["code"] in contracts_by_code]
        history = fetch_history_features(api, history_contracts, settings.kbar_days)
    finally:
        api.logout()

    snapshot_fields = [
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
    ]
    write_csv(output_dir / "snapshots.csv", snapshot_rows, snapshot_fields)

    groups = {"daytrade": [], "overnight": [], "weekly": []}
    excluded = []
    for row in snapshot_rows:
        for mode in groups:
            candidate, reasons = make_candidate(row, mode, settings, history.get(row["code"]))
            if candidate:
                groups[mode].append(candidate)
            elif reasons:
                excluded.append({"mode": mode, **row, "exclude_reasons": ",".join(reasons)})

    for mode in groups:
        groups[mode].sort(key=lambda item: item.score, reverse=True)
        write_candidates(output_dir, f"{mode}_andidates" if False else f"{mode}_candidates", groups[mode])

    write_candidates(output_dir, "candidates", groups["daytrade"])
    write_csv(output_dir / "excluded.csv", excluded, list(excluded[0].keys()) if excluded else ["mode", "code", "name", "exclude_reasons"])
    write_report(output_dir / "screening_report.md", run_time, simulation, settings, groups, len(excluded))

    print(f"output_dir={output_dir}")
    print(
        f"snapshots={len(snapshot_rows)} "
        f"daytrade={len(groups['daytrade'])} overnight={len(groups['overnight'])} "
        f"weekly={len(groups['weekly'])} excluded={len(excluded)}"
    )
    for mode in ("daytrade", "overnight", "weekly"):
        print(f"[{mode}]")
        for row in groups[mode][:5]:
            print(
                f"{row.level} {row.code} {row.name} score={row.score:.1f} "
                f"entry={row.entry_price:.2f} target={row.target_price:.2f} rr={row.rr_ratio:.2f}"
            )


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
