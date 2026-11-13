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

2026-11-11 correction: the "Monday and Thursday" Wild Money schedule quoted above is STALE — the
live `ris-draw-p1.ilotterydrawri.com` API itself (re-checked directly, not re-trusting the old
page quote) shows real, actually-drawn `results` for Wild Money on 10 CONSECUTIVE calendar days
(2026-10-31 through 2026-11-10, every single day), each at a consistent ~18:50 ET `drawTime` —
Wild Money now draws DAILY, not twice a week. This doesn't change any existing stored field (no
code here ever computed a "next Monday/Thursday" schedule), but next_draw_date added below is
sourced directly from the API's own real `drawTime`/`estimatedJackpot` for the nearest still-open
(not-yet-drawn) draw rather than from any assumed weekly pattern, so it's correct either way.
`estimatedJackpot` is in CENTS (confirmed by cross-referencing consecutive days' values, e.g.
9,800,000/9,200,000/8,700,000 escalating by a realistic ~$5-10k/day when divided by 100 — i.e.
$98,000/$92,000/$87,000 — versus an implausible $9.8M/$9.2M/$8.7M jackpot for this small-state
game if taken at face value).
"""
import datetime as dt
import json
import logging
import time
import zoneinfo
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ri_scraper")

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
REQUEST_TIMEOUT_SEC = 15
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 5
API_URL = "https://ris-draw-p1.ilotterydrawri.com/api/v2/draw-games/draws?previous-draws=5&next-draws=2"
OUTPUT_DIR = Path("data") / "rhode-island"
MAX_HISTORY = 100

# Numbers session times — re-confirmed via web search against rilot.com's own game pages (same
# figures already quoted in this file's docstring, 2026-10-16): Midday 1:30 PM ET, Evening
# 7:29 PM ET, both daily.
RI_TZ = zoneinfo.ZoneInfo("America/New_York")
NUMBERS_SESSION_TIMES = [("midday", 13, 30), ("evening", 19, 29)]


def compute_ri_numbers_next_draw() -> tuple:
    """Returns (next_draw_date, next_session) for whichever Numbers session comes next from now."""
    now = dt.datetime.now(RI_TZ)
    candidates = []
    for session, hour, minute in NUMBERS_SESSION_TIMES:
        t = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if t <= now:
            t += dt.timedelta(days=1)
        candidates.append((t, session))
    next_time, next_session = min(candidates, key=lambda c: c[0])
    return next_time.date().isoformat(), next_session


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

    # Next Wild Money draw: the soonest still-open (not-yet-drawn) "Wild Money" entry's own real
    # `drawTime`/`estimatedJackpot` — sourced directly from the live API, not an assumed weekly
    # pattern (see module doc's 2026-11-11 correction). `estimatedJackpot` is in cents.
    open_wild_money = [
        d for d in draws
        if d.get("gameName") == "Wild Money" and not d.get("results") and d.get("drawTime")
    ]
    next_wild_money_date = None
    next_wild_money_jackpot = None
    if open_wild_money:
        soonest = min(open_wild_money, key=lambda d: d["drawTime"])
        next_wild_money_date = dt.datetime.fromtimestamp(
            soonest["drawTime"] / 1000, tz=RI_TZ
        ).date().isoformat()
        est = soonest.get("estimatedJackpot")
        if est is not None:
            next_wild_money_jackpot = est / 100

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
        for e in wild_money_entries:
            if next_wild_money_date is not None:
                e["next_draw_date"] = next_wild_money_date
            if next_wild_money_jackpot is not None:
                e["next_jackpot_amount"] = next_wild_money_jackpot
        save_entries("wild-money", wild_money_entries, ("draw_date",))
    else:
        errors.append(("wild-money", "无数据"))

    if numbers_entries:
        next_draw_date, next_session = compute_ri_numbers_next_draw()
        for e in numbers_entries:
            e["next_draw_date"] = next_draw_date
            e["next_session"] = next_session
        save_entries("numbers", numbers_entries, ("draw_date", "session"))
    else:
        errors.append(("numbers", "无数据"))

    if errors:
        raise RuntimeError(f"{len(errors)} 个游戏抓取失败: {errors}")


if __name__ == "__main__":
    main()
