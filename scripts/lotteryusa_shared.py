"""
Shared parser for lotteryusa.com's `.c-draw-card` results-table markup — reused by nj_scraper.py,
nc_scraper.py, and oh_scraper.py. All three states' own games are pulled from lotteryusa.com rather
than each state's own official site:
  - NC: official nclottery.com confirmed blocked (403 on every plain request, same WAF-style block
    class as Montana/Tennessee/Oklahoma/Pennsylvania before it) — this project's established
    fallback precedent for that class of block.
  - OH: official ohiolottery.com itself returns 200, but its winning-numbers page renders results
    client-side with no embedded JSON/API endpoint found within this round's research budget —
    NOT a confirmed Cloudflare/WAF block like NC/MT, just a pragmatic choice; worth re-investigating
    the real API later if this fallback ever needs replacing.
  - NJ: official njlottery.com also returns 200, but (like Ohio) renders winning numbers via an
    Underscore.js template fed by a JS variable with no plain-HTTP-visible JSON source found.

Unlike `mt_scraper.py`'s single-card (`select_one`) fetch (a smaller incremental per-run grab),
this parses EVERY `.c-draw-card` row on the page in one request — lotteryusa.com's game pages
render a full results table (confirmed up to ~150 rows per page), not just the latest draw.
"""
import datetime as dt
import re
from typing import Optional

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
REQUEST_TIMEOUT_SEC = 15
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 5


def fetch_html(url: str) -> str:
    import requests
    import time
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_SEC)
            resp.raise_for_status()
            return resp.text
        except Exception as e:  # noqa: BLE001
            last_err = e
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SEC * attempt)
    raise RuntimeError(f"重试多次后仍然失败: {url}") from last_err


def _parse_date(text: str) -> str:
    m = re.search(r"([A-Za-z]{3,9})\s+(\d{1,2}),\s*(\d{4})", text.strip())
    if not m:
        raise ValueError(f"日期格式不对: {text!r}")
    month_name, day, year = m.groups()
    month = dt.datetime.strptime(month_name[:3], "%b").month
    return dt.date(int(year), month, int(day)).isoformat()


def parse_draw_cards(html: str, max_entries: int = 100) -> list[dict]:
    """Returns a list of dicts, newest first, each shaped:
      {
        "draw_date": "yyyy-MM-dd",
        "boxes": {"<title-or-'' >": [int, ...], ...},   # ball-box title ("" when untitled, e.g.
                                                           # Pick 3's single box; "Main draw"/
                                                           # "Double Play" when a page has two)
        "bonus_number": Optional[int],      # Fireball/Bullseye-style single `.c-result__bonus` ball
        "bonus_label": Optional[str],       # the <abbr title="..."> text, e.g. "Fireball"/"Bullseye"
        "prize_text": Optional[str],        # raw "$X" top-prize/jackpot text, if present
      }
    """
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    rows = soup.select(".c-draw-card")
    out = []
    for row in rows[:max_entries]:
        date_el = row.select_one(".c-draw-card__draw-date-sub")
        if date_el is None:
            continue
        try:
            draw_date = _parse_date(date_el.get_text(strip=True))
        except ValueError:
            continue

        boxes: dict[str, list[int]] = {}
        for box in row.select(".c-draw-card__ball-box"):
            title_el = box.select_one(".c-draw-card__ball-title")
            title = title_el.get_text(strip=True) if title_el else ""
            balls = [
                int(b.get_text(strip=True))
                for b in box.select("ul.c-draw-card__ball-list > li.c-ball")
                if b.get_text(strip=True).isdigit()
            ]
            boxes[title] = balls

        bonus_number: Optional[int] = None
        bonus_label: Optional[str] = None
        bonus_el = row.select_one(".c-result__bonus")
        if bonus_el is not None:
            abbr = bonus_el.select_one(".c-result__bonus-abbr")
            bonus_label = abbr.get("title") if abbr else None
            ball = bonus_el.select_one(".c-ball")
            if ball is not None and ball.get_text(strip=True).isdigit():
                bonus_number = int(ball.get_text(strip=True))

        prize_text = None
        prize_el = row.select_one(".c-draw-card__prize-value")
        if prize_el:
            prize_text = prize_el.get_text(strip=True)

        out.append({
            "draw_date": draw_date,
            "boxes": boxes,
            "bonus_number": bonus_number,
            "bonus_label": bonus_label,
            "prize_text": prize_text,
        })
    return out


def dedup_merge(existing: list[dict], new_entries: list[dict], key_field: str, max_history: int) -> list[dict]:
    seen = {e[key_field] for e in new_entries}
    merged = new_entries + [e for e in existing if e.get(key_field) not in seen]
    return merged[:max_history]
