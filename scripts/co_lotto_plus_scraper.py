"""
Colorado Lotto+ 抓取 — coloradolottery.com 官方站直接抓

⚠️ 合规说明：coloradolottery.com 的 Terms of Service 第3.11条明确禁止
"robot, spider, scraper" 访问本站——这个跟 Arkansas 当初用 lotteryusa.com
（第三方聚合站条款禁止）性质不完全一样，这里是官网自己明文禁止。本脚本是在
用户已经被明确告知这条禁令、并主动选择按较低频率（一天几次，只在官方开奖
时间前后抓）继续抓取事实性开奖数据的前提下写的——不是没查条款。如果
coloradolottery.com 以后明确来信要求停止，应立即停止，不要辩解。

规则：Lotto+ 主赛是6个号码从1-40抽出，无特殊球。官网同时会显示一个付费可选
的"Plus"副抽奖（单独再开一组6个号码，中奖池更小）——这个脚本只抓主赛号码，
不抓Plus（类似 Powerball Double Play 性质的独立副玩法，不在这次范围内）。

页面结构干净、服务端直接渲染：最新一期的详情链接 href 里直接带了 ISO 格式的
开奖日期（如 /en/games/lotto/drawings/2026-10-07/），不需要解析"Wednesday,
October 7"这种没带年份、容易出错的文字日期。
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
log = logging.getLogger("co_lotto_plus_scraper")

URL = "https://www.coloradolottery.com/en/games/lotto/"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
REQUEST_TIMEOUT_SEC = 15
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 5

OUTPUT_DIR = Path("data")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


@dataclasses.dataclass
class LottoPlusResult:
    game: str
    draw_date: str  # ISO yyyy-MM-dd, taken straight from the drawing-detail link's own URL
    numbers: list[int]  # 6个
    jackpot_amount: Optional[int]  # 整数美元，如 2800000
    fetched_at: str
    source_url: str
    next_draw_date: Optional[str] = None  # ISO yyyy-MM-dd
    next_jackpot_amount: Optional[int] = None
    next_cash_value: Optional[int] = None

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def _resolve_month_day_year(month_day_text: str, after_iso_date: str) -> Optional[str]:
    """页面顶部 "Next Drawing" 横幅只给"Wednesday, November 11"这种不带年份的文字，
    年份要结合"抓到的最近一期实际开奖日期"(after_iso_date，一定带年份)推断：下一期
    肯定在最近一期之后，如果按"最近一期的年份"算出来的日期反而落在它之前或等于它，
    说明跨年了，年份要+1。"""
    m = re.search(r"([A-Za-z]+)\s+(\d{1,2})\s*$", month_day_text.strip())
    if not m:
        return None
    month_name, day_str = m.group(1), m.group(2)
    try:
        after_date = dt.date.fromisoformat(after_iso_date)
        candidate = dt.datetime.strptime(f"{month_name} {day_str} {after_date.year}", "%B %d %Y").date()
        if candidate <= after_date:
            candidate = dt.datetime.strptime(f"{month_name} {day_str} {after_date.year + 1}", "%B %d %Y").date()
        return candidate.isoformat()
    except ValueError:
        return None


def _parse_next_draw_banner(soup) -> dict:
    """页面顶部的 jackpotWrapper/nextDate 横幅——跟历史开奖列表(allDraws)是页面里
    完全独立的两块DOM，class名不会和历史记录的 .drawJackpot 冲突。"""
    result = {"next_draw_date_text": None, "next_jackpot_amount": None, "next_cash_value": None}

    next_date_el = soup.select_one(".nextDate.panel strong, .nextDate strong")
    if next_date_el:
        result["next_draw_date_text"] = next_date_el.get_text(strip=True)

    jackpot_wrapper = soup.select_one(".jackpotWrapper")
    if jackpot_wrapper:
        amount_el = jackpot_wrapper.select_one(".amount")
        if amount_el:
            m = re.search(r"\$([\d,]+)", amount_el.get_text())
            if m:
                result["next_jackpot_amount"] = int(m.group(1).replace(",", ""))
        cash_el = jackpot_wrapper.select_one(".amountCash")
        if cash_el:
            m = re.search(r"\$([\d,]+)", cash_el.get_text())
            if m:
                result["next_cash_value"] = int(m.group(1).replace(",", ""))

    return result


def fetch() -> LottoPlusResult:
    import requests
    from bs4 import BeautifulSoup

    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(URL, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_SEC)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")

            link = soup.select_one('a[href*="/games/lotto/drawings/"]')
            if not link:
                raise ValueError("页面里没找到最新一期的开奖详情链接")
            m = re.search(r"/drawings/(\d{4}-\d{2}-\d{2})/", link.get("href", ""))
            if not m:
                raise ValueError(f"开奖详情链接格式不对: {link.get('href')}")
            draw_date = m.group(1)

            # 主赛号码是 href 所在 <a class="panel"> 容器里第一组 .draw .drawNumber（Plus 的号码
            # 在后面单独一个 .drawDate 文本是"Plus"的区块里，不抓）。
            panel = link if "panel" in (link.get("class") or []) else link.find_parent("a", class_="panel")
            if panel is None:
                panel = soup.select_one("a.panel")
            balls = panel.select(".draw .drawNumber")[:6]
            numbers = [int(b.get_text(strip=True)) for b in balls]

            jackpot_amount = None
            jackpot_el = panel.select_one(".drawJackpot")
            if jackpot_el:
                m2 = re.search(r"\$([\d,]+)", jackpot_el.get_text())
                if m2:
                    jackpot_amount = int(m2.group(1).replace(",", ""))

            if len(numbers) != 6:
                raise ValueError(f"主号码应为6个，抓到 {len(numbers)} 个: {numbers}")

            banner = _parse_next_draw_banner(soup)
            next_draw_date = None
            if banner["next_draw_date_text"]:
                next_draw_date = _resolve_month_day_year(banner["next_draw_date_text"], draw_date)

            return LottoPlusResult(
                game="Colorado Lotto+",
                draw_date=draw_date,
                numbers=numbers,
                jackpot_amount=jackpot_amount,
                fetched_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                source_url=URL,
                next_draw_date=next_draw_date,
                next_jackpot_amount=banner["next_jackpot_amount"],
                next_cash_value=banner["next_cash_value"],
            )
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("第 %d 次尝试失败: %s", attempt, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SEC * attempt)
    raise RuntimeError("重试多次后仍然失败") from last_err


def validate(result: LottoPlusResult) -> None:
    if len(result.numbers) != 6:
        raise ValueError(f"主号码应为6个: {result.numbers}")
    if not all(1 <= n <= 40 for n in result.numbers):
        raise ValueError(f"号码超出 1-40 范围: {result.numbers}")


MAX_HISTORY = 100


def save_results(result: LottoPlusResult) -> Path:
    out_dir = OUTPUT_DIR / "colorado" / "lotto-plus"
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
    merged = [new_entry] + history
    merged = merged[:MAX_HISTORY]

    out_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2))
    log.info("已保存: %s (%d 条记录)", out_path, len(merged))
    return out_path


def main():
    result = fetch()
    validate(result)
    log.info("抓取结果: %s", result.to_dict())
    save_results(result)


if __name__ == "__main__":
    main()
