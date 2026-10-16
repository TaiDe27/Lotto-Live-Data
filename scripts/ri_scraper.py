"""
Rhode Island own-games — sourced from the real official IGT/Brightstar-platform backend. The
user's own browser devtools captured this endpoint on the `ris.p1.awc.lotteryservices.net`
("convenienceCloudApi") host, which turned out to require authenticated-session access (401
without a login cookie, confirmed directly). The SAME endpoint path works with zero auth on a
sibling host found in rilot.com's own page source (`drawcloudApiUrl`):
`ris-draw-p1.ilotterydrawri.com` — used here instead, same real backend, just the public
draw-results host rather than the account/wagering one. The earlier-guessed `rilottery.com` is a
parked/for-sale domain, NOT RI's real site — the real site is `rilot.com`.

Quirk found while building: `previous-draws=N` means "N previous draws PER GAME", not N total —
a small N (confirmed working: 5) already covers every game including biweekly ones like Wild
Money; a large N instead gets dominated by Keno's high-frequency entries and silently drops Wild
Money out of the response window entirely. Also: completed draws can carry `status` values other
than "PAYABLE" (e.g. "CLOSED") while still having real `results` — filter on `results` being
present, not on a specific status string.

ONLY Wild Money and Numbers are built — an explicit user scope decision after reviewing this
API's full game list (Mega Millions, Lucky For Life, Keno, Powerball, Bingo, Xs and Os, Million 4
Life all also appear in the same endpoint, but are either already-covered multistate games or
draw dozens+ times a day; deliberately not built here).

Confirmed real mechanics/schedule (quoted from rilot.com's own game pages, 2026-10-16):
  - Wild Money: "Drawings held Monday and Thursday at 9:00 p.m. ET!" — resolves an earlier
    conflicting "daily" vs "Tue/Thu/Sat" claim from third-party research. "Select 5 numbers from 1
    to 38... 5 numbers are drawn from that field of 38... The Extra ball is drawn from the
    remaining 33 numbers" — a standard 5-of-38 + 1 special ball drawn from the same overall pool
    (functionally a plain special ball since it can never duplicate a main number by construction).
  - Numbers: "Midday Drawings take place daily at 1:30 p.m. ... Evening Drawings are aired on
    WPRI-TV 12 at 7:29 p.m." — a 4-digit base draw (confirmed: "4 Digit - Choose 1 digit in each
    column") offering Front/Back sub-wagers (2/3-digit) on the same underlying draw; only the base
    4-digit straight/box tiers are modeled this round.
"""
import datetime as dt
import json
import logging
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ri_scraper")

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
REQUEST_TIMEOUT_SEC = 15
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 5
API_URL = "https://ris-draw-p1.ilotterydrawri.com/api/v2/draw-games/draws?previous-draws=5&next-draws=0"
OUTPUT_DIR = Path("data") / "rhode-island"
MAX_HISTORY = 100


def fetch_draws() -> list:
    import requests

    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(API_URL, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_SEC)
            resp.raise_for_status()
            data = resp.json()
            draws = data.get("draws")
            if not isinstance(draws, list):
                raise ValueError(f"响应里没有 draws 数组: {list(data.keys())}")
            return draws
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("第 %d 次尝试失败: %s", attempt, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SEC * attempt)
    raise RuntimeError("重试多次后仍然失败") from last_err


def save_entries(subdir: str, new_entries: list, key_fields: tuple) -> Path:
    out_dir = OUTPUT_DIR / subdir
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "latest.json"
    history: list = []
    if out_path.exists():
        try:
            existing = json.loads(out_path.read_text())
            if isinstance(existing, list):
                history = existing
        except (json.JSONDecodeError, OSError):
            history = []
    new_keys = {tuple(e.get(k) for k in key_fields) for e in new_entries}
    history = [h for h in history if tuple(h.get(k) for k in key_fields) not in new_keys]
    merged = (new_entries + history)[:MAX_HISTORY]
    out_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2))
    log.info("已保存: %s (%d 条记录)", out_path, len(merged))
    return out_path


def main():
    draws = fetch_draws()
    now = dt.datetime.now(dt.timezone.utc).isoformat()

    wild_money_entries = []
    numbers_entries = []

    for d in draws:
        if not d.get("results"):
            continue
        game_name = d.get("gameName")
        draw_time_ms = d.get("drawTime")
        draw_date = dt.datetime.fromtimestamp(draw_time_ms / 1000, tz=dt.timezone.utc).date().isoformat() if draw_time_ms else None
        result = d["results"][0]

        if game_name == "Wild Money":
            primary = result.get("primary", [])
            main_nums = [int(x) for x in primary if not str(x).startswith("PL-")]
            extra_raw = next((x for x in primary if str(x).startswith("PL-")), None)
            wild_money_entries.append({
                "draw_date": draw_date,
                "numbers": main_nums,
                "special_number": int(extra_raw.split("-", 1)[1]) if extra_raw else None,
                "draw_id": d.get("id"), "fetched_at": now, "source_url": API_URL,
            })
        elif game_name == "Numbers":
            session = (d.get("name") or "").lower()
            if session not in ("midday", "evening"):
                continue
            numbers_entries.append({
                "draw_date": draw_date, "session": session,
                "digits": list(result.get("primary", [])),
                "draw_id": d.get("id"), "fetched_at": now, "source_url": API_URL,
            })

    errors = []
    if wild_money_entries:
        save_entries("wild-money", wild_money_entries, ("draw_date",))
    else:
        errors.append(("wild-money", "无数据"))

    if numbers_entries:
        save_entries("numbers", numbers_entries, ("draw_date", "session"))
    else:
        errors.append(("numbers", "无数据"))

    if errors:
        raise RuntimeError(f"{len(errors)} 个游戏抓取失败: {errors}")


if __name__ == "__main__":
    main()
