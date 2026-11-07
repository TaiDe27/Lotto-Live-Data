"""
Minnesota own-games — mnlottery.com relaunched on a new WordPress theme sometime around
2026-11-06/07 (confirmed from the page's own asset URLs, e.g. `wp-content/themes/minnesota-
lottery/dist/blocks/...`), which silently broke this scraper: `.card--lottery`/`.lottery-number-
list`/`.lottery-drawing` don't exist anywhere in the new markup at all (confirmed via a plain
`requests` fetch of all 3 game pages - 0 matches for any of those class names).

More importantly, the winning numbers are no longer in the server-rendered HTML `requests` sees at
all — a plain fetch returns the page shell with none of the actual numbers in it. Confirmed via
Playwright that the results widget renders them client-side a few seconds after load. So this now
needs the same real-Chromium approach as the Powerball-family/PA/WI scrapers, not just a CSS
selector fix.

New format (confirmed 2026-11-07 from Playwright's rendered `innerText` on all 3 real game pages -
no stable class names found to hang a BeautifulSoup selector on, so this parses the plain visible
text by its fixed label words instead, same general approach as the Powerball-family scripts):
  <GAME NAME> ESTIMATED JACKPOT       (Pick 3 instead shows "PICK 3 TOP PRIZE", no dollar line
  $980,000                             after it at all - it has no jackpot figure to show)
  11/6/26 RESULTS
  15                                  (one number per line, `expected_count` of them)
  25
  35
  37
  39
  NEXT DRAWING
  11/9 @ 6:17 PM CST                 (or "Today @ 6:17 PM CST")

North 5 draws daily; Gopher 5 draws Mon/Wed/Fri; Pick 3 draws daily, ONE session per day (no
midday/evening split, unlike most other states' Pick 3 — confirmed from the same page, which shows
exactly one block of numbers).
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
log = logging.getLogger("mn_scraper")

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
REQUEST_TIMEOUT_SEC = 15
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 5
OUTPUT_DIR = Path("data") / "minnesota"
MAX_HISTORY = 100

GAMES = [
    # (url, output subdir, field name for numbers, expected count)
    ("https://www.mnlottery.com/games/lotto/gopher-5", "gopher5", 5),
    ("https://www.mnlottery.com/games/lotto/north-5", "north5", 5),
    ("https://www.mnlottery.com/games/lotto/pick-3", "pick3", 3),
]


@dataclasses.dataclass
class MnResult:
    draw_date: str  # yyyy-MM-dd
    numbers: list[int]
    next_jackpot_amount: Optional[int]
    fetched_at: str
    source_url: str

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def fetch_one(url: str, expected_count: int) -> MnResult:
    from playwright.sync_api import sync_playwright

    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch()
                try:
                    page = browser.new_page(user_agent=USER_AGENT)
                    page.goto(url, timeout=REQUEST_TIMEOUT_SEC * 1000, wait_until="domcontentloaded")
                    page.get_by_text("RESULTS").first.wait_for(timeout=REQUEST_TIMEOUT_SEC * 1000)
                    text = page.inner_text("body")
                finally:
                    browser.close()

            date_m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{2})\s+RESULTS", text, re.IGNORECASE)
            if not date_m:
                raise ValueError("没找到 'M/D/YY RESULTS' 开奖日期行")
            mm, dd, yy = date_m.groups()
            draw_date = f"20{yy}-{int(mm):02d}-{int(dd):02d}"

            after_results = text[date_m.end():]
            next_drawing_m = re.search(r"\n\s*NEXT DRAWING", after_results, re.IGNORECASE)
            numbers_block = after_results[:next_drawing_m.start()] if next_drawing_m else after_results
            numbers = [int(tok) for tok in numbers_block.splitlines() if tok.strip().isdigit()]
            if len(numbers) != expected_count:
                raise ValueError(f"应为 {expected_count} 个号码，抓到 {len(numbers)} 个: {numbers}")

            # The jackpot dollar figure (when this game has one at all - Pick 3 doesn't) sits
            # somewhere above the date/RESULTS line, as the last "$amount" before it - not grabbing
            # the first "$..." found on the whole page, which could be an unrelated promo figure
            # further up in the header/nav.
            before_results = text[:date_m.start()]
            jackpot_matches = re.findall(r"\$([\d,]+)", before_results)
            next_jackpot = int(jackpot_matches[-1].replace(",", "")) if jackpot_matches else None

            return MnResult(
                draw_date=draw_date,
                numbers=numbers,
                next_jackpot_amount=next_jackpot,
                fetched_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                source_url=url,
            )
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("第 %d 次尝试失败 (%s): %s", attempt, url, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SEC * attempt)
    raise RuntimeError(f"重试多次后仍然失败: {url}") from last_err


def save_results(subdir: str, result: MnResult) -> Path:
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

    new_entry = result.to_dict()
    history = [h for h in history if h.get("draw_date") != new_entry["draw_date"]]
    merged = ([new_entry] + history)[:MAX_HISTORY]
    out_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2))
    log.info("已保存: %s (%d 条记录)", out_path, len(merged))
    return out_path


def main():
    errors = []
    for url, subdir, expected_count in GAMES:
        try:
            result = fetch_one(url, expected_count)
            log.info("%s 抓取结果: %s", subdir, result.to_dict())
            save_results(subdir, result)
        except Exception as e:  # noqa: BLE001
            log.error("%s 失败: %s", subdir, e)
            errors.append((subdir, e))
    if errors:
        raise RuntimeError(f"{len(errors)} 个游戏抓取失败: {errors}")


if __name__ == "__main__":
    main()
