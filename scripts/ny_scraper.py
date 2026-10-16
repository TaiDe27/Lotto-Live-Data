"""
New York own-games — sourced from New York State's own open-data portal, `data.ny.gov` (Socrata),
NOT nylottery.ny.gov itself. Found via web search rather than scraping nylottery.ny.gov's HTML
directly: NY State publishes official, no-auth, daily-refreshed JSON datasets for exactly these
games, each confirmed directly against real live data this round:
  - `hsys-3def` ("Lottery Daily Numbers/Win-4 Winning Numbers: Beginning 1980") — ONE row per day
    with BOTH games' BOTH sessions as separate fields (`midday_daily`, `evening_daily`,
    `midday_win_4`, `evening_win_4`), not four separate rows.
  - `dg63-4siq` ("Lottery Take 5 Winning Numbers: Beginning 1992") — one row per day with
    `midday_winning_numbers`/`evening_winning_numbers` as space-separated strings.
  - `bycu-cw7c` ("Lottery Pick 10 Winning Numbers: Beginning 1987") — one row per day,
    `winning_numbers` holds all 20 drawn numbers (space-separated) for this "pick 10 of 1-80,
    match against 20 drawn" game.

Confirmed mechanics:
  - Numbers / Win 4: standard digit games (3/4-digit), Midday 2:30pm / Evening 10:30pm ET. No
    Fireball-equivalent add-on (confirmed: no bonus/extra-digit field anywhere in this dataset or in
    nylottery.org's own "How to Play" page).
  - Take 5: 5 of 1-39, $1/play, Midday 2:30pm / Evening 10:30pm ET, no special ball.
  - Pick 10: pick 10 of 1-80; NY draws 20 numbers and pays by how many of the PLAYER's 10 numbers
    appear among those 20 — this project's generic `matchCombination` formula (a plain
    `Set(drawing.mainNumbers).intersection(entry.mainNumbers).count`) handles this correctly with no
    new `MatchRule.Kind` needed, since it never assumes the picked-count and drawn-count are equal;
    `matchRule: null` is correct here. $1/play, daily 8:30pm ET. FIXED (non-progressive) top prize
    of $500,000 for matching all 10 — `isJackpotTier: false`, not pulled from a live jackpot field.
    Real tier table (match 10/9/8/7/6/0 pay; 1-5 pay nothing — a real, intentional "match zero
    wins" tier, same keno-family shape, not an omission): 10=$500,000, 9=$6,000, 8=$300, 7=$40,
    6=$10, 0=$4.

Deliberately NOT built: Quick Draw (confirmed every-4-minutes draws, same class as every other
state's 4-minute keno-style game skipped this project).
"""
import datetime as dt
import json
import logging
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ny_scraper")

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
REQUEST_TIMEOUT_SEC = 15
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 5
OUTPUT_DIR = Path("data") / "new-york"
MAX_HISTORY = 100


def _get_json(url: str):
    import requests
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_SEC)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("第 %d 次尝试失败 (%s): %s", attempt, url, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SEC * attempt)
    raise RuntimeError(f"重试多次后仍然失败: {url}") from last_err


def _draw_date(row: dict) -> str:
    # Socrata returns e.g. "2026-10-14T00:00:00.000"
    return row["draw_date"][:10]


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


def scrape_numbers_win4():
    rows = _get_json("https://data.ny.gov/resource/hsys-3def.json?$limit=100&$order=draw_date%20DESC")
    numbers_out, win4_out = [], []
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    for r in rows:
        draw_date = _draw_date(r)
        if r.get("midday_daily") and r.get("evening_daily"):
            numbers_out.append({
                "draw_date": draw_date,
                "midday_digits": [int(d) for d in r["midday_daily"].zfill(3)],
                "evening_digits": [int(d) for d in r["evening_daily"].zfill(3)],
                "fetched_at": now,
            })
        if r.get("midday_win_4") and r.get("evening_win_4"):
            win4_out.append({
                "draw_date": draw_date,
                "midday_digits": [int(d) for d in r["midday_win_4"].zfill(4)],
                "evening_digits": [int(d) for d in r["evening_win_4"].zfill(4)],
                "fetched_at": now,
            })
    if not numbers_out or not win4_out:
        raise ValueError("Numbers/Win4 没解析到任何记录")
    save_results("numbers", numbers_out)
    save_results("win4", win4_out)
    log.info("numbers: 最新一条 %s", numbers_out[0])
    log.info("win4: 最新一条 %s", win4_out[0])


def scrape_take5():
    rows = _get_json("https://data.ny.gov/resource/dg63-4siq.json?$limit=100&$order=draw_date%20DESC")
    out = []
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    for r in rows:
        midday = r.get("midday_winning_numbers")
        evening = r.get("evening_winning_numbers")
        if not midday or not evening:
            continue
        out.append({
            "draw_date": _draw_date(r),
            "midday_numbers": [int(n) for n in midday.split()],
            "evening_numbers": [int(n) for n in evening.split()],
            "fetched_at": now,
        })
    if not out:
        raise ValueError("Take 5 没解析到任何记录")
    save_results("take5", out)
    log.info("take5: 最新一条 %s", out[0])


def scrape_pick10():
    rows = _get_json("https://data.ny.gov/resource/bycu-cw7c.json?$limit=100&$order=draw_date%20DESC")
    out = []
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    for r in rows:
        nums = r.get("winning_numbers")
        if not nums:
            continue
        numbers = [int(n) for n in nums.split()]
        if len(numbers) != 20:
            continue
        out.append({"draw_date": _draw_date(r), "numbers": numbers, "fetched_at": now})
    if not out:
        raise ValueError("Pick 10 没解析到任何记录")
    save_results("pick10", out)
    log.info("pick10: 最新一条 %s", out[0])


def main():
    errors = []
    for name, fn in [("numbers/win4", scrape_numbers_win4), ("take5", scrape_take5), ("pick10", scrape_pick10)]:
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            log.error("%s 失败: %s", name, e)
            errors.append((name, e))
    if errors:
        raise RuntimeError(f"{len(errors)} 个游戏抓取失败: {errors}")


if __name__ == "__main__":
    main()
