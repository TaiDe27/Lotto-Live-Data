"""
Maryland own-games — lotteryusa.com. mdlottery.com's own site IS reachable with a browser UA
(confirmed 2026-10-14; this project's identifying UA gets 403), but its real results feed loads via
a separate call this session didn't have time to reverse-engineer — lotteryusa.com has clean
per-game pages for everything except Cash POP (404 there), so Cash POP is deferred, not guessed.

Bonus Match 5 and Multi-Match reuse the same `tr.c-draw-card` structure already proven for every
other lotteryusa.com-sourced state in this repo.
"""
import dataclasses
import datetime as dt
import json
import logging
import re
import time
from pathlib import Path
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("md_scraper")

USER_AGENT = "LottoLiveApp/0.1 (+mailto:YOUR-CONTACT-EMAIL@example.com)"
REQUEST_TIMEOUT_SEC = 15
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 5
FETCH_COUNT = 10
OUTPUT_DIR = Path("data") / "maryland"

MONEY_PATTERN = r"\$?([\d,.]+\s*(?:Million|Billion|million|billion)?)"


def iso_date(raw: str) -> str:
    return dt.datetime.strptime(raw, "%b %d, %Y").strftime("%Y-%m-%d")


def parse_money(raw: str) -> Optional[str]:
    if not raw:
        return None
    m = re.search(MONEY_PATTERN, raw)
    return m.group(1).strip() if m else None


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


def fetch_digit_session(url: str, session: str, count: int, label: str) -> list[dict]:
    rows = _fetch_rows(url)
    out = []
    for row in rows:
        date_el = row.select_one(".c-draw-card__draw-date-sub")
        if not date_el:
            continue
        draw_date = iso_date(date_el.get_text(strip=True))
        balls = row.select(".c-draw-card__ball-list > li.c-ball") or row.select("li.c-ball")
        digits = [int(b.get_text(strip=True)) for b in balls]
        if len(digits) != count:
            continue
        out.append({"game": label, "draw_date": draw_date, "session": session, "digits": digits,
                    "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(), "source_url": url})
    return out


def fetch_pool(url: str, label: str, main_count: int, has_bonus: bool) -> list[dict]:
    rows = _fetch_rows(url)
    out = []
    for row in rows:
        date_el = row.select_one(".c-draw-card__draw-date-sub")
        if not date_el:
            continue
        draw_date = iso_date(date_el.get_text(strip=True))
        box = row.select_one(".c-draw-card__ball-box") or row
        balls = box.select("li.c-ball")
        if has_bonus:
            bonus_el = box.select_one(".c-result__bonus .c-ball")
            main_balls = [b for b in balls if b is not bonus_el]
            numbers = [int(b.get_text(strip=True)) for b in main_balls[:main_count]]
            bonus_number = int(bonus_el.get_text(strip=True)) if bonus_el else None
        else:
            numbers = [int(b.get_text(strip=True)) for b in balls[:main_count]]
            bonus_number = None
        if len(numbers) != main_count:
            continue
        prize_el = row.select_one(".c-draw-card__prize-value")
        jackpot_amount = parse_money(prize_el.get_text(strip=True)) if prize_el else None
        entry = {"game": label, "draw_date": draw_date, "numbers": numbers,
                  "jackpot_amount": jackpot_amount,
                  "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(), "source_url": url}
        if has_bonus:
            entry["bonus_number"] = bonus_number
        out.append(entry)
    return out


def save(subdir: str, new_entries: list[dict], key_fn) -> None:
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
    new_keys = {key_fn(e) for e in new_entries}
    history = [h for h in history if key_fn(h) not in new_keys]
    merged = (new_entries + history)[:100]
    out_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2))
    log.info("已保存: %s (%d 条，本次更新 %d 条)", out_path, len(merged), len(new_entries))


def main():
    by_date = lambda e: e["draw_date"]
    by_date_session = lambda e: (e["draw_date"], e["session"])

    save("pick3", fetch_digit_session("https://www.lotteryusa.com/maryland/midday-pick-3/", "midday", 3, "Pick 3 (Midday)")
                  + fetch_digit_session("https://www.lotteryusa.com/maryland/pick-3/", "evening", 3, "Pick 3 (Evening)"), by_date_session)
    save("pick4", fetch_digit_session("https://www.lotteryusa.com/maryland/midday-pick-4/", "midday", 4, "Pick 4 (Midday)")
                  + fetch_digit_session("https://www.lotteryusa.com/maryland/pick-4/", "evening", 4, "Pick 4 (Evening)"), by_date_session)
    save("pick5", fetch_digit_session("https://www.lotteryusa.com/maryland/midday-pick-5/", "midday", 5, "Pick 5 (Midday)")
                  + fetch_digit_session("https://www.lotteryusa.com/maryland/pick-5/", "evening", 5, "Pick 5 (Evening)"), by_date_session)
    save("bonusmatch5", fetch_pool("https://www.lotteryusa.com/maryland/bonus-match-5/", "Bonus Match 5", 5, True), by_date)
    save("multimatch", fetch_pool("https://www.lotteryusa.com/maryland/multi-match/", "Multi-Match", 6, False), by_date)
    log.info("完成")


if __name__ == "__main__":
    main()
