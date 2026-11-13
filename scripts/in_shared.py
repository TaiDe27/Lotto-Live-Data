"""
Indiana (Hoosier Lottery) 共享抓取逻辑 — hoosierlottery.com 官方前端服务端渲染页面

合规说明：hoosierlottery.com 的 robots.txt 对所有路径完全放行(User-agent: * 没有
任何Disallow规则)，用本脚本的自报UA直接curl拿到200，没有Cloudflare JS challenge。
它自己的Terms and Conditions里没查到禁止自动化访问/爬虫/spider/data mining的条款
(只有常规的"不要转载我们的版权内容"声明)——跟Florida/Colorado/Delaware/Georgia同一档
清白来源，不需要像Arkansas/Connecticut/Illinois那样退而求其次用lotteryusa.com。
没有公开JSON API，数据是服务端直接渲染的静态HTML，requests+BeautifulSoup就够。

页面上的日期是"Wednesday, October 7th"这种格式——没有年份、带序数词后缀——跟这个仓库
其它所有脚本的"Oct 7, 2026"格式都不一样。iso_date()去掉序数词后缀，再用"离参考日期
(默认UTC今天)最近的那个年份"推断年份：抓到的都是已经开完奖的结果，不会提前拿到未来
日期，所以如果按当前年份算出来的月日比参考日期晚超过2天，就说明实际是去年同一天。这个
启发式在跨年边界(比如12月31号抓到跨到1月1号的情况极少发生，一天最多抓4次，错了下一次
也会被新一期数据覆盖)不是高风险点。
"""

import datetime as dt
import logging
import re
import time
from typing import Optional
from zoneinfo import ZoneInfo

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("in_shared")

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
REQUEST_TIMEOUT_SEC = 15
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 5
MAX_HISTORY = 100

# Daily 3 / Daily 4 / Quick Draw 共享同一个开奖时间表——分别直接抓取
# hoosierlottery.com 对应游戏页面上的文字确认的（不是互相假设抄来的）：
# "Midday drawings occur daily around 1:20 PM ET" / "Evening drawings occur
# daily around 11:00 PM ET"，三个游戏页面用词完全一致，且都是"Daily"/
# "available 7 days a week"——没有跳过的日子。用 America/Indiana/Indianapolis
# （常年随美东时区走，含DST）锚定本地时间。
INDIANA_TZ = ZoneInfo("America/Indiana/Indianapolis")
MIDDAY_DRAW_TIME = dt.time(13, 20)
EVENING_DRAW_TIME = dt.time(23, 0)


def next_draw(now: Optional[dt.datetime] = None) -> tuple[str, str]:
    """Daily 3 / Daily 4 / Quick Draw 的下一期日期+场次：如果当前(本地时间)还没到
    今天的Midday(1:20pm)，下一期就是今天Midday；如果已经过了Midday但没到今天
    Evening(11pm)，下一期是今天Evening；如果已经过了今天Evening，下一期是明天
    Midday。每天都开两期，没有跳过的日子。"""
    now = dt.datetime.now(INDIANA_TZ) if now is None else now.astimezone(INDIANA_TZ)
    today = now.date()
    midday_dt = dt.datetime.combine(today, MIDDAY_DRAW_TIME, tzinfo=INDIANA_TZ)
    evening_dt = dt.datetime.combine(today, EVENING_DRAW_TIME, tzinfo=INDIANA_TZ)
    if now < midday_dt:
        return today.isoformat(), "midday"
    if now < evening_dt:
        return today.isoformat(), "evening"
    return (today + dt.timedelta(days=1)).isoformat(), "midday"

_ORDINAL_RE = re.compile(r"(\d+)(st|nd|rd|th)", re.IGNORECASE)


def iso_date(raw: str, reference: Optional[dt.date] = None) -> str:
    """"Wednesday, October 7th" -> "2026-10-07"（没有年份，按上面文档说明推断）。"""
    if reference is None:
        reference = dt.datetime.now(dt.timezone.utc).date()
    cleaned = _ORDINAL_RE.sub(r"\1", raw).replace(",", "")
    parts = cleaned.split()
    month_day = " ".join(parts[-2:])  # "October 7"
    parsed = dt.datetime.strptime(f"{month_day} {reference.year}", "%B %d %Y").date()
    if (parsed - reference).days > 2:
        parsed = parsed.replace(year=reference.year - 1)
    return parsed.isoformat()


def fetch_html(url: str) -> str:
    import requests

    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_SEC, allow_redirects=True)
            resp.raise_for_status()
            return resp.text
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("第 %d 次尝试失败 (%s): %s", attempt, url, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SEC * attempt)
    raise RuntimeError(f"重试多次后仍然失败: {url}") from last_err


def fetched_at_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def resolve_forward_date(month_name: str, day: str, after_iso_date: str) -> Optional[str]:
    """跟上面 iso_date() 反过来——"下一期"的日期一定在 after_iso_date(最近一期实际
    开奖日，带年份)之后，不是之前，所以年份推断方向要反过来：按 after_iso_date 的
    年份算出来的日期如果落在它之前或等于它，说明跨年了，年份要+1。"""
    try:
        after_date = dt.date.fromisoformat(after_iso_date)
        candidate = dt.datetime.strptime(f"{month_name} {day} {after_date.year}", "%B %d %Y").date()
        if candidate <= after_date:
            candidate = dt.datetime.strptime(f"{month_name} {day} {after_date.year + 1}", "%B %d %Y").date()
        return candidate.isoformat()
    except ValueError:
        return None


def find_next_draw_info(soup, after_iso_date: str) -> dict:
    """每个开奖游戏自己的页面(非ticker)上有一块独立的"大标题"区域——跟站点共用的
    .jackpot-alert-card滚动条是两个完全不同的DOM，这里的 class 名没有"alert"/
    "title"前缀冲突：<span class="jackpot-amount">$<span class="counter"
    data-amount="85000"></span></span> + <span class="drawing-date">Next Drawing
    - Friday, November 13th</span>（没带年份，用 after_iso_date 推断）。实测确认
    (2026-11-11，Hoosier Lotto / CA$H 5 页面)：这个滚存jackpot数字跟"目前还没被
    中走、下一期能中的金额"是同一个数——中走了才会重置变小，没有单独的"下一期预估"
    跟"现在"分开两个数字的情况，所以直接拿来当 next_jackpot_amount 用。页面上没找
    到任何 Cash Value / 现金选项文字——hoosierlottery.com 的滚存游戏都是一次性
    现金支付，没有年金/现金二选一。"""
    next_jackpot_amount = None
    amount_el = soup.select_one("span.jackpot-amount .counter")
    if amount_el and amount_el.get("data-amount"):
        try:
            next_jackpot_amount = int(amount_el["data-amount"])
        except ValueError:
            next_jackpot_amount = None

    next_draw_date = None
    drawing_date_el = soup.select_one(".drawing-date")
    if drawing_date_el:
        m = re.search(r"Next Drawing\s*-\s*[A-Za-z]+,\s*([A-Za-z]+)\s+(\d{1,2})", drawing_date_el.get_text(strip=True))
        if m:
            next_draw_date = resolve_forward_date(m.group(1), m.group(2), after_iso_date)

    return {"next_draw_date": next_draw_date, "next_jackpot_amount": next_jackpot_amount}


def find_jackpot_amount(soup, game_path_fragment: str) -> Optional[str]:
    """Every hoosierlottery.com page carries the same site-wide "Current Jackpots" ticker
    (`.jackpot-alert-card`, one per rolling-jackpot game, each linking to that game's own
    `/games/draw/<slug>/` page) — more reliable than anything on the individual game's own
    draw page, where the jackpot figure turned out to be client-side-injected (static HTML
    just showed a bare "$" placeholder). `game_path_fragment` is matched against each card's
    link href (e.g. "hoosier-lotto", "cash-5") to find the right one; returns the raw
    "$1,200,000"-style text, or None if the ticker ever changes shape or doesn't carry this
    game (e.g. a fixed-prize game like Hoosier Lotto +PLUS has no ticker entry at all)."""
    for card in soup.select(".jackpot-alert-card"):
        link = card.select_one(".jackpot-title a")
        if not link or game_path_fragment not in (link.get("href") or ""):
            continue
        amount_el = card.select_one(".jackpot-amount")
        if amount_el:
            return amount_el.get_text(strip=True)
    return None
