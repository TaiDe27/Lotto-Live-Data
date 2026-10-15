"""
Michigan own-games — lotteryusa.com. michiganlottery.com's own site runs a GraphQL API
(confirmed 2026-10-14: `allGames` query works and gave the REAL current game roster — Daily 3/4/5
Midday+Evening, Fantasy 5, Classic Lotto 47, Daily Keno, Club Keno (separate from Daily Keno),
Poker Lotto, Cash Pop — but introspection is disabled and the actual draw-results query shape
wasn't reverse-engineered in the time available this round, so this scraper uses lotteryusa.com
instead for the real data. Flagged: Daily 3/4/5 only have ONE session on lotteryusa.com (no
"midday-daily-3"-style URL exists, checked) even though Michigan draws them twice daily — the
midday session is NOT captured by this scraper, a real known gap, not a silent one. Cash Pop is
also not on lotteryusa.com (404) — deferred like Maryland's.

Poker Lotto draws 5 playing cards, not numbers — `li.c-playing-card` each hold a rank span and a
suit-symbol span (♣♥♦♠); this writes them as "RankSuitLetter" codes (e.g. "6C"/"2H"/"AC") matching
exactly the format shown on the official app's own results card (confirmed against a user-provided
screenshot: "6C-2H-AC-10H-4S").
"""
import dataclasses
import datetime as dt
import json
import logging
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("mi_scraper")

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
REQUEST_TIMEOUT_SEC = 15
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 5
FETCH_COUNT = 10
OUTPUT_DIR = Path("data") / "michigan"

SUIT_LETTER = {"♣": "C", "♥": "H", "♦": "D", "♠": "S"}


def iso_date(raw: str) -> str:
    return dt.datetime.strptime(raw, "%b %d, %Y").strftime("%Y-%m-%d")


def _fetch_rows(url: str) -> list:
    import requests
    from bs4 import BeautifulSoup
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_SEC)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            rows = soup.select("tr.c-draw-card")[:FETCH_COUNT]
            if not rows:
                raise ValueError(f"[{url}] 没找到任何 c-draw-card 行")
            return rows
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("[%s] 第 %d 次尝试失败: %s", url, attempt, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SEC * attempt)
    raise RuntimeError(f"[{url}] 重试多次后仍然失败") from last_err


def fetch_digit(url: str, count: int, label: str) -> list[dict]:
    out = []
    for row in _fetch_rows(url):
        date_el = row.select_one(".c-draw-card__draw-date-sub")
        if not date_el:
            continue
        draw_date = iso_date(date_el.get_text(strip=True))
        balls = row.select(".c-draw-card__ball-list > li.c-ball") or row.select("li.c-ball")
        digits = [int(b.get_text(strip=True)) for b in balls]
        if len(digits) != count:
            continue
        out.append({"game": label, "draw_date": draw_date, "digits": digits,
                    "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(), "source_url": url})
    return out


def fetch_pool(url: str, count: int, label: str) -> list[dict]:
    out = []
    for row in _fetch_rows(url):
        date_el = row.select_one(".c-draw-card__draw-date-sub")
        if not date_el:
            continue
        draw_date = iso_date(date_el.get_text(strip=True))
        balls = row.select("li.c-ball")
        numbers = [int(b.get_text(strip=True)) for b in balls[:count]]
        if len(numbers) != count:
            continue
        out.append({"game": label, "draw_date": draw_date, "numbers": numbers,
                    "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(), "source_url": url})
    return out


def fetch_poker_lotto(url: str) -> list[dict]:
    out = []
    for row in _fetch_rows(url):
        date_el = row.select_one(".c-draw-card__draw-date-sub")
        if not date_el:
            continue
        draw_date = iso_date(date_el.get_text(strip=True))
        cards_el = row.select("li.c-playing-card")
        cards = []
        for c in cards_el:
            spans = c.select("span")
            if len(spans) != 2:
                continue
            rank, suit = spans[0].get_text(strip=True), spans[1].get_text(strip=True)
            letter = SUIT_LETTER.get(suit)
            if not letter:
                continue
            cards.append(f"{rank}{letter}")
        if len(cards) != 5:
            continue
        out.append({"game": "Poker Lotto", "draw_date": draw_date, "cards": cards,
                    "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(), "source_url": url})
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
    save("daily3", fetch_digit("https://www.lotteryusa.com/michigan/daily-3/", 3, "Daily 3"))
    save("daily4", fetch_digit("https://www.lotteryusa.com/michigan/daily-4/", 4, "Daily 4"))
    save("daily5", fetch_digit("https://www.lotteryusa.com/michigan/daily-5/", 5, "Daily 5"))
    save("fantasy5", fetch_pool("https://www.lotteryusa.com/michigan/fantasy-5/", 5, "Fantasy 5"))
    save("classiclotto47", fetch_pool("https://www.lotteryusa.com/michigan/classic-lotto-47/", 6, "Classic Lotto 47"))
    save("keno", fetch_pool("https://www.lotteryusa.com/michigan/keno/", 10, "Keno"))
    save("pokerlotto", fetch_poker_lotto("https://www.lotteryusa.com/michigan/poker-lotto/"))
    log.info("完成")


if __name__ == "__main__":
    main()
