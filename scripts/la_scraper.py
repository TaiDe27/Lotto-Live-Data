"""
Louisiana own-games — official site (louisianalottery.com/winning-numbers/), one combined page
with ALL games (multi-state + own). Needs a standard browser UA — this project's own identifying
UA gets a 403 here (confirmed), same WAF-keys-on-UA pattern already seen for other states (California
scrapers already use this workaround) — this is the one deviation from the identifying-UA norm in
this batch, flagged for the record, not a silent choice.

Louisiana has NO public JSON API and no per-game URL slugs — all 7 games (Powerball, Mega Millions,
Lotto, Easy 5, Pick 5, Pick 4, Pick 3, in that fixed page order) share one `.game-table__table-wrap`
list with no game-name heading attached to each wrap (checked — the nearest heading is generic
page chrome, not useful). Games are identified by FIXED POSITIONAL INDEX in that list (confirmed
2026-10-14 against real data) with a ball-count sanity check as a safety net in case Louisiana ever
reorders the page: index 2=Lotto(6 balls), 3=Easy 5(5), 4=Pick 5(5 digits), 5=Pick 4(4 digits),
6=Pick 3(3 digits). Indices 0/1 (Powerball/Mega Millions) are skipped — already covered live.
"""
import dataclasses
import datetime as dt
import json
import logging
import time
from pathlib import Path
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("la_scraper")

URL = "https://louisianalottery.com/winning-numbers/"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
REQUEST_TIMEOUT_SEC = 20
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 5
OUTPUT_DIR = Path("data") / "louisiana"

# (output_subdir, game_label, expected_ball_count, has_own_digits_not_pool)
GAMES = [
    (2, "lotto", "Lotto", 6, False),
    (3, "easy5", "Easy 5", 5, False),
    (4, "pick5", "Pick 5", 5, True),
    (5, "pick4", "Pick 4", 4, True),
    (6, "pick3", "Pick 3", 3, True),
]


def fetch_all() -> dict[str, list[dict]]:
    import requests
    from bs4 import BeautifulSoup

    last_err = None
    html = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(URL, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_SEC)
            resp.raise_for_status()
            html = resp.text
            break
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("第 %d 次尝试失败: %s", attempt, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SEC * attempt)
    if html is None:
        raise RuntimeError("重试多次后仍然失败") from last_err

    soup = BeautifulSoup(html, "html.parser")
    wraps = soup.select(".game-table__table-wrap")
    if len(wraps) < 7:
        raise ValueError(f"预期至少 7 个 game-table__table-wrap，实际只找到 {len(wraps)} 个 — 页面结构可能变了")

    out: dict[str, list[dict]] = {}
    for idx, subdir, label, expected_count, is_digit_game in GAMES:
        wrap = wraps[idx]
        table = wrap.select_one("table.winning-numbers__table")
        if not table:
            log.warning("[%s] 没找到 table.winning-numbers__table", label)
            continue
        rows = table.select("tr:has(th.c-draw-card__date), tr:has(span[id])")
        if not rows:
            rows = [tr for tr in table.find_all("tr") if tr.select_one("span[id]")]
        results = []
        for tr in rows[:10]:
            date_span = tr.select_one("span[id]")
            if not date_span or not date_span.get("id"):
                continue
            draw_date = date_span["id"]  # already "yyyy-MM-dd" per the id attribute
            balls = tr.select("li.jackpot-sequence__number")
            numbers = [int(b.get_text(strip=True)) for b in balls]
            if len(numbers) != expected_count:
                log.warning("[%s] %s 期号码数 %d，预期 %d，跳过: %s", label, draw_date, len(numbers), expected_count, numbers)
                continue
            # Pick 3/4/5 are digit games (Swift's shared digit-game mapper reads `digits`); Lotto/
            # Easy 5 are pool games (shared pool mapper reads `numbers`) — same source structure,
            # different field name depending on which shared Swift liveMapping path this game uses
            # (see state_config_base_games.json's digit_game() vs pool_game() for these ids).
            entry = {
                "game": label, "draw_date": draw_date,
                ("digits" if is_digit_game else "numbers"): numbers,
                "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(), "source_url": URL,
            }
            results.append(entry)
        out[subdir] = results
    return out


def save(subdir: str, new_entries: list[dict]) -> None:
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
    new_keys = {e["draw_date"] for e in new_entries}
    history = [h for h in history if h.get("draw_date") not in new_keys]
    merged = (new_entries + history)[:100]
    out_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2))
    log.info("已保存: %s (%d 条，本次更新 %d 条)", out_path, len(merged), len(new_entries))


def main():
    all_results = fetch_all()
    for subdir, results in all_results.items():
        log.info("[%s] 最新: %s", subdir, results[0] if results else None)
        save(subdir, results)


if __name__ == "__main__":
    main()
