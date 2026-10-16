"""
New Mexico own-games — nmlottery.com's own pages embed a results widget hosted at
`nmlotterydynamic.sks.com` (their lottery systems vendor, SCIQ/SKS), found by reading the page's own
embedded iframe `src` rather than guessing — a plain, server-rendered page, no JS execution needed:
`https://nmlotterydynamic.sks.com/rts/games/pastdrawings/search/?game=<id>` returns up to ~100 past
drawings in one request (`<h3>MM/DD/YYYY SESSION</h3><ul class="winning-numbers">...</ul>` rows).

Confirmed real game IDs on this widget (from its own dropdown, which also lists retired games —
`hotlotto`, `luckynumbers` — and legacy `pick3`/`pick4` IDs alongside the CURRENT `pick3plus`/
`pick4plus` ones; nmlottery.com's own current navigation only surfaces "Pick 3 Plus"/"Pick 4 Plus"
as the live products, so only those two are scraped here):
  - `pick3plus` / `pick4plus`: standard digit games, Day 1:00pm / Evening 9:30pm MT. "Plus" refers
    to optional Front-Pair/Back-Pair side bets, not a Fireball-style drawn add-on digit — confirmed
    no extra/replacement digit anywhere in this widget's rendering.
  - `roadrunnercash`: 5 of 1-37, $1/play, daily 9:30pm MT, no special ball. No live jackpot amount
    field found on this widget (consistent with this project's precedent for several other small
    in-state jackpot games, e.g. Minnesota's Gopher 5/North 5 — a static `jackpotDescription` is
    used instead of a live-fetched jackpot number).

New Mexico observes Mountain Time WITH DST (`America/Denver`), unlike Arizona's fixed-offset zone.
"""
import datetime as dt
import json
import logging
import re
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("nm_scraper")

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
REQUEST_TIMEOUT_SEC = 15
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 5
OUTPUT_DIR = Path("data") / "new-mexico"
MAX_HISTORY = 100

ROW_RE = re.compile(
    r'<h3>(\d{1,2}/\d{1,2}/\d{4})\s*([A-Z]*)</h3>\s*<ul class="winning-numbers">(.*?)</ul>',
    re.S,
)
BALL_RE = re.compile(r"<li>(\d+)</li>")


def _get(url: str) -> str:
    import requests
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_SEC)
            resp.raise_for_status()
            return resp.text
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("第 %d 次尝试失败 (%s): %s", attempt, url, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SEC * attempt)
    raise RuntimeError(f"重试多次后仍然失败: {url}") from last_err


def fetch_rows(game_id: str) -> list[dict]:
    url = f"https://nmlotterydynamic.sks.com/rts/games/pastdrawings/search/?game={game_id}"
    html = _get(url)
    out = []
    for m in ROW_RE.finditer(html):
        date_str, session, balls_html = m.groups()
        month, day, year = date_str.split("/")
        draw_date = dt.date(int(year), int(month), int(day)).isoformat()
        numbers = [int(b) for b in BALL_RE.findall(balls_html)]
        if not numbers:
            continue
        out.append({
            "draw_date": draw_date,
            "session": session.strip() or None,
            "numbers": numbers,
            "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "source_url": url,
        })
    if not out:
        raise ValueError(f"没解析到任何记录 (game={game_id})")
    return out[:MAX_HISTORY]


def save_results(subdir: str, new_entries: list[dict], key_fields: tuple[str, ...]) -> Path:
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
    seen = {tuple(e.get(k) for k in key_fields) for e in new_entries}
    merged = new_entries + [e for e in existing if tuple(e.get(k) for k in key_fields) not in seen]
    merged = merged[:MAX_HISTORY]
    out_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2))
    log.info("已保存: %s (%d 条记录)", out_path, len(merged))
    return out_path


def main():
    errors = []

    for subdir, game_id in [("pick3plus", "pick3plus"), ("pick4plus", "pick4plus")]:
        try:
            rows = fetch_rows(game_id)
            save_results(subdir, rows, ("draw_date", "session"))
            log.info("%s: 最新一条 %s", subdir, rows[0])
        except Exception as e:  # noqa: BLE001
            log.error("%s 失败: %s", subdir, e)
            errors.append((subdir, e))
        time.sleep(1)

    try:
        rows = fetch_rows("roadrunnercash")
        save_results("roadrunner-cash", rows, ("draw_date",))
        log.info("roadrunner-cash: 最新一条 %s", rows[0])
    except Exception as e:  # noqa: BLE001
        log.error("roadrunner-cash 失败: %s", e)
        errors.append(("roadrunner-cash", e))

    if errors:
        raise RuntimeError(f"{len(errors)} 个游戏抓取失败: {errors}")


if __name__ == "__main__":
    main()
