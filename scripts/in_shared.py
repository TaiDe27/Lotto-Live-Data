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

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("in_shared")

USER_AGENT = "LottoLiveApp/0.1 (+mailto:YOUR-CONTACT-EMAIL@example.com)"
REQUEST_TIMEOUT_SEC = 15
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 5
MAX_HISTORY = 100

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
