"""
Shared parser for lotteryusa.com's `.c-draw-card` results-table markup — reused by nj_scraper.py,
nc_scraper.py, and oh_scraper.py. All three states' own games are pulled from lotteryusa.com rather
than each state's own official site:

2026-11-10 update: added `parse_next_draw_info` — a second, independent parser for the
`.c-state-short-info` "Next draw" widget that sits above the results table on every lotteryusa.com
game page. This one is imported directly by individual per-game scrapers (not just the three listed
above) that need next-draw-date/next-jackpot-estimate data for their own `latest.json`, regardless of
whether that scraper otherwise uses `parse_draw_cards`/`fetch_html` from this module or has its own
duplicated history-table parsing.
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


def _parse_next_draw_date_text(text: str) -> Optional[str]:
    """Handles every date phrasing seen across lotteryusa.com's game pages: 'Today at 11:34 pm EST',
    'Tomorrow, Nov 11, 2026', and 'Thursday, Nov 12, 2026'. 'Today'/'Tomorrow' are resolved against
    the scraper's own run date (UTC), same approximation this project already accepts elsewhere for
    relative schedule anchoring — exact to within a day at the US/UTC boundary, which every consumer
    of this field already tolerates (the app anchors the real draw hour separately, per game)."""
    text = text.strip()
    lowered = text.lower()
    today = dt.datetime.now(dt.timezone.utc).date()
    if lowered.startswith("today"):
        return today.isoformat()
    if lowered.startswith("tomorrow"):
        return (today + dt.timedelta(days=1)).isoformat()
    m = re.search(r"([A-Za-z]{3,9})\s+(\d{1,2}),\s*(\d{4})", text)
    if not m:
        return None
    month_name, day, year = m.groups()
    try:
        month = dt.datetime.strptime(month_name[:3], "%b").month
    except ValueError:
        return None
    return dt.date(int(year), month, int(day)).isoformat()


def parse_next_draw_info(html: str) -> dict:
    """Parses the `.c-state-short-info` widget present on lotteryusa.com's per-game pages (confirmed
    across Illinois/Arkansas/Georgia/Texas/Connecticut/Pennsylvania game pages on 2026-11-10): always
    exactly two rows, 'Next <game> draw' -> date text, and 'Next est. jackpot' (or 'Next top prize' on
    non-jackpot-pool games) -> a '$X' amount. No cash-value row exists anywhere on lotteryusa.com for
    any game checked, so this never returns one — callers should leave next_cash_value unset/None.
    Returns {"next_draw_date": "yyyy-MM-dd" | None, "next_jackpot_amount": raw "$X" text | None}.
    """
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    block = soup.select_one(".c-state-short-info")
    if block is None:
        return {"next_draw_date": None, "next_jackpot_amount": None}

    next_draw_date = None
    next_jackpot_amount = None
    for row in block.select(".c-state-short-info__row"):
        title_el = row.select_one(".c-state-short-info__title")
        val_el = row.select_one(".c-state-short-info__subtitle")
        if title_el is None or val_el is None:
            continue
        title = title_el.get_text(strip=True).lower()
        value = val_el.get_text(strip=True)
        if "draw" in title:
            next_draw_date = _parse_next_draw_date_text(value)
        elif "jackpot" in title or "prize" in title:
            # 2026-11-11 发现: 刚好在"今晚要开的这期"之前、网站自己还没算出预估
            # 奖金的那个小窗口里，这一格实际内容是纯文字"Updating"(不是"$X")——
            # 亲眼在 Illinois Lucky Day Lotto Evening 页面上抓到过(confirmed
            # live)。这种非"$"开头的值不是真的奖金数字，不能当 next_jackpot_amount
            # 存下来，否则下游会把"Updating"这几个字当金额去解析。宁可留空(下一次
            # 抓取、网站更新后会有真的数字)，不要把这串文字硬塞进去。
            if value.strip().startswith("$"):
                next_jackpot_amount = value

    return {"next_draw_date": next_draw_date, "next_jackpot_amount": next_jackpot_amount}
