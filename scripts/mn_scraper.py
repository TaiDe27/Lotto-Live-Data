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
    next_draw_date: Optional[str] = None  # yyyy-MM-dd

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def _resolve_mm_dd(month: str, day: str, after_iso_date: str) -> Optional[str]:
    """"NEXT DRAWING"后面的"11/9 @ 6:17 PM CST"不带年份，用最近一期实际开奖日期
    (after_iso_date，一定带年份)推断：按它的年份算出来的日期如果落在它之前或等于
    它，说明跨年了，年份要+1。"""
    try:
        after_date = dt.date.fromisoformat(after_iso_date)
        candidate = dt.date(after_date.year, int(month), int(day))
        if candidate <= after_date:
            candidate = dt.date(after_date.year + 1, int(month), int(day))
        return candidate.isoformat()
    except ValueError:
        return None


def _parse_next_draw_date(after_next_drawing: str, after_iso_date: str) -> Optional[str]:
    """"NEXT DRAWING"标签后面跟的是"11/9 @ 6:17 PM CST"或"Today @ 6:17 PM CST"这种
    文字（实测确认，2026-11-07/11-13，Gopher 5 / North 5 页面）。"Today"按抓取时的
    UTC日期算（跟 lotteryusa_shared.py 的"Today"处理一样，容许一天以内的误差，这个
    项目其它地方已经接受这个近似）。"""
    m_mmdd = re.search(r"\b(\d{1,2})/(\d{1,2})\s*@", after_next_drawing)
    if m_mmdd:
        return _resolve_mm_dd(m_mmdd.group(1), m_mmdd.group(2), after_iso_date)
    m_rel = re.search(r"\b(Today|Tomorrow)\b", after_next_drawing, re.IGNORECASE)
    if m_rel:
        today = dt.datetime.now(dt.timezone.utc).date()
        if m_rel.group(1).lower() == "today":
            return today.isoformat()
        return (today + dt.timedelta(days=1)).isoformat()
    return None


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
            #
            # 2026-11-13 发现+修复: 原来的 r"\$([\d,]+)" 正则在真实的"$1.16 MILLION"
            # 这种写法上会出真实bug——"."不在字符类里，只会匹配到小数点前的"1"，
            # 把1.16M存成了"1"（亲眼在Gopher 5实际抓到的页面文本上confirmed）。
            # North 5/Pick 5这类不到$1M的奖池页面上是纯"$27,000"这种整数写法，
            # 之前凑巧没暴露这个bug。现在改成能同时处理两种写法，再统一换算成整数
            # 美元存，不改变这个字段本来就是"存整数"的约定。
            before_results = text[:date_m.start()]
            jackpot_matches = list(re.finditer(r"\$([\d,]+(?:\.\d+)?)\s*(million|thousand)?", before_results, re.IGNORECASE))
            next_jackpot = None
            if jackpot_matches:
                amount_str, unit = jackpot_matches[-1].groups()
                value = float(amount_str.replace(",", ""))
                if unit:
                    value *= 1_000_000 if unit.lower() == "million" else 1_000
                next_jackpot = int(round(value))

            next_draw_date = None
            if next_drawing_m:
                next_draw_date = _parse_next_draw_date(after_results[next_drawing_m.end():], draw_date)

            return MnResult(
                draw_date=draw_date,
                numbers=numbers,
                next_jackpot_amount=next_jackpot,
                fetched_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                source_url=url,
                next_draw_date=next_draw_date,
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
