"""
New York own-games — sourced from nylottery.ny.gov's OWN real backend API (found via the user's
browser devtools, 2026-10-16), replacing the previous data.ny.gov (Socrata) open-data source. That
Socrata source (`hsys-3def`) was confirmed stuck a full day+ behind real draws; this real API is
extremely fresh (results posted ~10-20 minutes after the actual draw).

Endpoint: `https://nylottery.ny.gov/nyl-api/games/all/draws` — ONE request returns every NY game's
recent draws at once, keyed by gameKey. No auth needed (plain GET). `robots.txt` on this domain is
fully permissive (`Allow: /`) — this is the genuine official state site, unlike `nylottery.org` (a
third-party reseller whose ToS explicitly bans scraping — confirmed and rejected this session, never
use that domain).

Each draw object: `drawTime` (date only, time always 00:00:00 — NOT useful for time-of-day),
`drawPeriod` (1=midday, 2=evening for Win4/Numbers/Take5, confirmed by cross-checking `resultDate`
against each game's officially-stated close_time — see below; 0=single daily/weekly session for
Pick10/Lotto), `resultDate` (epoch ms — this is when results post, close to each game's SALES-CLOSE
time, NOT the broadcast draw time; do not use it as a draw-time source), `status` (22 = official
result posted, 4 = pending/no result yet — skip non-22 entries), `results[0].primary`/`.secondary`.

**Real draw times used for Scheduler.gs (confirmed from nylottery.ny.gov's own
`/drupal-api/api/games?_format=json` catalog endpoint's `draw_time` field — literal official text,
not inferred from timestamps)**:
  - Win4 / Numbers / Take 5: `draw_time: "Midday (2:30 PM) Evening (10:30 PM)"` — midday=drawPeriod 1,
    evening=drawPeriod 2 (cross-validated: `close_time: "2:15 PM & 10:20 PM"` matches each session's
    observed `resultDate` time-of-day almost exactly, confirming the drawPeriod<->session mapping).
  - Pick 10: `draw_time: "8:30 PM"` (single session, drawPeriod 0).
  - Lotto (NY's own in-state 6/59+bonus jackpot game, NEWLY ADDED this pass — previously missed,
    found via the same catalog endpoint): `draw_time: "8:15 PM"`, `draw_days: ["Wednesdays",
    "Saturdays"]`. Real pick shape cross-confirmed via lotteryusa.com/new-york/lotto/: "1 to 59"
    appears for both the 6 main numbers and the Bonus Number (drawn from the same 1-59 pool, not a
    separate smaller pool like Powerball's red ball). Real price per the catalog's own
    `pricing_information`: "Two Plays: $1" i.e. $0.50/play — unusually cheap, confirmed, not a typo.
  - Quick Draw: `draw_time: "Every 4 Mins"` — confirmed continuous, correctly stays out of scope
    (project's standing ≤10-draws/day rule).
  - Cash4Life: confirmed retired, not a gap. Catalog shows `alert_message: "Game Closed"`,
    `close_time: "CLOSED"`, `app_description_long_form_msg: "The final drawing for Cash4Life took
    place on 2/21/26."` — same class as this project's prior "Lucky for Life is retired" precedent
    (see KY research). Deliberately not built.

Output JSON shape UNCHANGED from the previous data.ny.gov-based scraper (zero Swift-side impact,
`BundledDataSource.swift` has no NY-specific hardcoded case — this state has always run through the
generic config-driven `liveMapping` path):
  - `data/new-york/numbers/latest.json`: `{draw_date, midday_digits:[3 ints], evening_digits:[3
    ints], fetched_at}` per day.
  - `data/new-york/win4/latest.json`: same shape, 4-digit.
  - `data/new-york/take5/latest.json`: `{draw_date, midday_numbers:[5 ints], evening_numbers:[5
    ints], fetched_at}`.
  - `data/new-york/pick10/latest.json`: `{draw_date, numbers:[20 ints], fetched_at}`.
  - `data/new-york/lotto/latest.json` (NEW): `{draw_date, numbers:[6 ints], bonus:int, fetched_at}`.

Deliberately NOT built: Quick Draw (4-min continuous), Cash4Life (retired), Raffle (out of scope,
not a regular recurring draw game).

2026-11-10 update: every game object in this endpoint's `data` dict also carries a top-level
`next_draw`/`last_draw` pair (epoch ms, confirmed real/live by cross-checking against each game's
official `draw_time`/`close_time` text from the `/drupal-api/api/games` catalog endpoint). It is
ONE shared value per game, not per session — for Win4/Numbers/Take5 (each modeled as two separate
App-side game entries, midday vs evening) it's bucketed here into separate
`midday_next_draw_date`/`evening_next_draw_date` fields by whether its ET hour falls before or
after `MIDDAY_EVENING_HOUR_CUTOFF` (the real sales-close times are ~2:15 PM and ~10:20 PM ET, with
a wide safety margin either side of the cutoff): an upcoming midday close means neither session has
drawn yet today, so both next-dates are today; an upcoming evening close means today's midday
already happened, so midday rolls to tomorrow while evening stays today. Pick 10 and Lotto are
single-session games, so their `next_draw` date is used directly as `next_draw_date`. No numeric
next-jackpot-estimate field was found anywhere in this endpoint or the `/drupal-api/api/games`
catalog for Take5/Lotto (their `odds_prizes` "Jackpot" row is pari-mutuel text like "Jackpot*"/
"$57,575.70**" with asterisked caveats, not a clean forward-looking estimate safe to re-publish) —
confirmed gap, left unmapped. Pick 10's own top prize is a fixed, non-progressive $500,000 (already
modeled as a flat `payoutTable` tier, not a `nextJackpot`), so it needs no jackpot wiring either.
"""
import datetime as dt
import json
import logging
import time
from pathlib import Path
from zoneinfo import ZoneInfo

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ny_scraper")

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
REQUEST_TIMEOUT_SEC = 20
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 5
OUTPUT_DIR = Path("data") / "new-york"
MAX_HISTORY = 100
DRAWS_URL = "https://nylottery.ny.gov/nyl-api/games/all/draws"
STATUS_OFFICIAL = 22


def _get_json(url: str):
    import requests
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}, timeout=REQUEST_TIMEOUT_SEC)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("第 %d 次尝试失败 (%s): %s", attempt, url, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SEC * attempt)
    raise RuntimeError(f"重试多次后仍然失败: {url}") from last_err


def _date_str(draw: dict) -> str:
    ms = draw["drawTime"]
    return dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc).strftime("%Y-%m-%d")


MIDDAY_EVENING_HOUR_CUTOFF = 18  # ET hour: below = upcoming midday close; at/above = upcoming evening close
NY_TZ = ZoneInfo("America/New_York")


def _next_draw_et(next_draw_ms) -> "dt.datetime | None":
    if not next_draw_ms:
        return None
    return dt.datetime.fromtimestamp(next_draw_ms / 1000, tz=dt.timezone.utc).astimezone(NY_TZ)


def _single_session_next_draw_date(game: dict) -> "str | None":
    """Pick 10 / Lotto: one session a day (or per draw day), so the game-level `next_draw` date is
    used as-is."""
    et = _next_draw_et(game.get("next_draw"))
    return et.date().isoformat() if et is not None else None


def _session_next_draw_dates(game: dict) -> "tuple[str, str] | tuple[None, None]":
    """Win4/Numbers/Take5: `next_draw` is ONE shared value (the nearer of the two sessions' sales
    closes) - see module docstring's 2026-11-10 update for the bucketing rationale."""
    et = _next_draw_et(game.get("next_draw"))
    if et is None:
        return None, None
    today_str = et.date().isoformat()
    if et.hour < MIDDAY_EVENING_HOUR_CUTOFF:
        return today_str, today_str
    tomorrow_str = (et.date() + dt.timedelta(days=1)).isoformat()
    return tomorrow_str, today_str


def save_results(subdir: str, new_entries: list[dict]) -> Path:
    out_dir = OUTPUT_DIR / subdir
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "latest.json"
    existing: list[dict] = []
    if out_path.exists():
        try:
            loaded = json.loads(out_path.read_text())
            if isinstance(loaded, list):
                existing = loaded
        except (json.JSONDecodeError, OSError):
            existing = []
    seen = {e["draw_date"] for e in new_entries}
    merged = (new_entries + [e for e in existing if e.get("draw_date") not in seen])[:MAX_HISTORY]
    out_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2))
    log.info("已保存: %s (%d 条记录)", out_path, len(merged))
    return out_path


def _official_results(draws: list[dict]) -> list[dict]:
    return [d for d in draws if d.get("status") == STATUS_OFFICIAL and d.get("results")]


def scrape_digit_pair(data: dict, game_key: str, subdir: str, digit_count: int):
    """Win4/Numbers: one row/day with both midday (drawPeriod 1) and evening (drawPeriod 2)."""
    draws = _official_results(data[game_key]["draws"])
    by_date: dict[str, dict] = {}
    for d in draws:
        date_str = _date_str(d)
        entry = by_date.setdefault(date_str, {"draw_date": date_str})
        digits = [int(x) for x in d["results"][0]["primary"]]
        if len(digits) != digit_count:
            continue
        if d["drawPeriod"] == 1:
            entry["midday_digits"] = digits
        elif d["drawPeriod"] == 2:
            entry["evening_digits"] = digits
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    midday_next, evening_next = _session_next_draw_dates(data[game_key])
    out = []
    for date_str in sorted(by_date, reverse=True):
        e = by_date[date_str]
        if "midday_digits" in e and "evening_digits" in e:
            e["fetched_at"] = now
            if midday_next:
                e["midday_next_draw_date"] = midday_next
            if evening_next:
                e["evening_next_draw_date"] = evening_next
            out.append(e)
    if not out:
        raise ValueError(f"{game_key} 没解析到任何完整(午+晚)记录")
    save_results(subdir, out)
    log.info("%s: 最新一条 %s", subdir, out[0])


def scrape_take5(data: dict):
    draws = _official_results(data["take5"]["draws"])
    by_date: dict[str, dict] = {}
    for d in draws:
        date_str = _date_str(d)
        entry = by_date.setdefault(date_str, {"draw_date": date_str})
        numbers = [int(x) for x in d["results"][0]["primary"]]
        if len(numbers) != 5:
            continue
        if d["drawPeriod"] == 1:
            entry["midday_numbers"] = numbers
        elif d["drawPeriod"] == 2:
            entry["evening_numbers"] = numbers
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    midday_next, evening_next = _session_next_draw_dates(data["take5"])
    out = []
    for date_str in sorted(by_date, reverse=True):
        e = by_date[date_str]
        if "midday_numbers" in e and "evening_numbers" in e:
            e["fetched_at"] = now
            if midday_next:
                e["midday_next_draw_date"] = midday_next
            if evening_next:
                e["evening_next_draw_date"] = evening_next
            out.append(e)
    if not out:
        raise ValueError("take5 没解析到任何完整(午+晚)记录")
    save_results("take5", out)
    log.info("take5: 最新一条 %s", out[0])


def scrape_pick10(data: dict):
    draws = _official_results(data["pick10"]["draws"])
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    next_draw_date = _single_session_next_draw_date(data["pick10"])
    out = []
    for d in draws:
        numbers = [int(x) for x in d["results"][0]["primary"]]
        if len(numbers) != 20:
            continue
        entry = {"draw_date": _date_str(d), "numbers": numbers, "fetched_at": now}
        if next_draw_date:
            entry["next_draw_date"] = next_draw_date
        out.append(entry)
    if not out:
        raise ValueError("pick10 没解析到任何记录")
    save_results("pick10", out)
    log.info("pick10: 最新一条 %s", out[0])


def scrape_lotto(data: dict):
    draws = _official_results(data["lotto"]["draws"])
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    next_draw_date = _single_session_next_draw_date(data["lotto"])
    out = []
    for d in draws:
        res = d["results"][0]
        numbers = [int(x) for x in res["primary"]]
        secondary = res.get("secondary") or []
        if len(numbers) != 6 or not secondary or secondary[0] == "":
            continue
        entry = {
            "draw_date": _date_str(d),
            "numbers": numbers,
            "bonus": int(secondary[0]),
            "fetched_at": now,
        }
        if next_draw_date:
            entry["next_draw_date"] = next_draw_date
        out.append(entry)
    if not out:
        raise ValueError("lotto 没解析到任何记录")
    save_results("lotto", out)
    log.info("lotto: 最新一条 %s", out[0])


def main():
    data = _get_json(DRAWS_URL)["data"]
    errors = []
    jobs = [
        ("win4", lambda: scrape_digit_pair(data, "win4", "win4", 4)),
        ("numbers", lambda: scrape_digit_pair(data, "numbers", "numbers", 3)),
        ("take5", lambda: scrape_take5(data)),
        ("pick10", lambda: scrape_pick10(data)),
        ("lotto", lambda: scrape_lotto(data)),
    ]
    for name, fn in jobs:
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            log.error("%s 失败: %s", name, e)
            errors.append((name, e))
    if errors:
        raise RuntimeError(f"{len(errors)} 个游戏抓取失败: {errors}")


if __name__ == "__main__":
    main()
