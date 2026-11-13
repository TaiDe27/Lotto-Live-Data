"""
Idaho Cash 抓取 — idaholottery.com 官方站直接抓

合规说明：idaholottery.com 的 robots.txt 是标准 Drupal 默认模板，没有对
/games/ 路径做任何限制；Terms and Conditions 全文检索 crawl/harvest/data
mining/spider/bot/scrape/automated 均无命中——没有禁止自动化访问的条款，
跟 Florida/Colorado 一样是干净站点。

规则：5 个号码从 1-45 抽出，无特殊球。每天 MT 时间 8:00pm 开奖一次。
Pari-mutuel 奖级：5 全中 Jackpot，4 中 $200，3 中 $5，2 中免费票——赔率
固定、不是靠本脚本抓的（页面没有逐期的小奖实际派奖金额，只有赔率规则，
写进 App 端 games.json 的 payoutTable 固定值）。

页面结构：/games/draw/idaho-cash 页面里有一个"Past Drawings"历史表格
(#tab4 里的 <table>)，每行是 MM/DD/YY 格式日期 + 5个<li>号码 + 当期 Jackpot
金额——比只抓"最新一期"那个顶部摘要卡片更好，一次能拿到最近 5 期历史，不用
等好多天才能攒出历史记录。
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
log = logging.getLogger("id_cash_scraper")

URL = "https://www.idaholottery.com/games/draw/idaho-cash"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
REQUEST_TIMEOUT_SEC = 15
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 5

OUTPUT_DIR = Path("data")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def iso_date(raw: str) -> str:
    """"10/09/26" -> "2026-10-09"."""
    return dt.datetime.strptime(raw.strip(), "%m/%d/%y").strftime("%Y-%m-%d")


def _resolve_month_day_year(month: str, day: str, after_iso_date: str) -> Optional[str]:
    """页面顶部"Next Draw: Wed/Nov 11"这种文字不带年份，结合最近一期实际开奖日期
    (after_iso_date，一定带年份)推断：算出来的日期如果落在它之前或等于它，说明
    跨年了，年份要+1。"""
    try:
        after_date = dt.date.fromisoformat(after_iso_date)
        candidate = dt.datetime.strptime(f"{month} {day} {after_date.year}", "%b %d %Y").date()
        if candidate <= after_date:
            candidate = dt.datetime.strptime(f"{month} {day} {after_date.year + 1}", "%b %d %Y").date()
        return candidate.isoformat()
    except ValueError:
        return None


def _parse_next_draw_info(html: str, latest_draw_date: str) -> dict:
    """顶部摘要卡片里有一块跟历史表格(#tab4)完全独立的 DOM——"Next Estimated
    Jackpot"(.jackpot__header 紧跟着的 <h5>) + "Next Draw: Wed/Nov 11"
    (.numbers__header.header2，不带年份，用最近一期的实际开奖日期推断年份)。
    实测确认(2026-11-10)：这一块没有任何"Cash Value"/现金选项文字——免责声明
    里写明"The Jackpot prize is paid in one lump-sum, cash payment"，也就是
    jackpot本身就是现金一次性领取，没有另外的"年金/现金"二选一，所以不设
    next_cash_value。"""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")

    next_jackpot_amount = None
    jackpot_header = soup.select_one(".jackpot__header")
    if jackpot_header and "next estimated jackpot" in jackpot_header.get_text(strip=True).lower():
        amount_el = jackpot_header.find_next_sibling("h5")
        if amount_el:
            m = re.search(r"\$([\d,]+)", amount_el.get_text())
            if m:
                next_jackpot_amount = int(m.group(1).replace(",", ""))

    next_draw_date = None
    header2 = soup.select_one(".numbers__header.header2")
    if header2:
        m = re.search(r"Next Draw:\s*[A-Za-z]{3}/([A-Za-z]{3})\s+(\d{1,2})", header2.get_text())
        if m:
            next_draw_date = _resolve_month_day_year(m.group(1), m.group(2), latest_draw_date)

    return {"next_draw_date": next_draw_date, "next_jackpot_amount": next_jackpot_amount}


@dataclasses.dataclass
class CashResult:
    game: str
    draw_date: str
    numbers: list[int]
    jackpot_amount: Optional[int]
    fetched_at: str
    source_url: str
    next_draw_date: Optional[str] = None
    next_jackpot_amount: Optional[int] = None

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def fetch() -> list[CashResult]:
    import requests

    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(URL, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_SEC)
            resp.raise_for_status()
            html = resp.text

            idx = html.find('id="tab4"')
            if idx < 0:
                raise ValueError("页面里没找到 Past Drawings 历史表格(#tab4)")
            chunk = html[idx:idx + 20000]

            rows = re.findall(
                r'<td data-title="Date">\s*([\d/]+)\s*</td>\s*<td data-title="Winning Numbers">\s*'
                r'<ul class="list-numbers[^"]*">(.*?)</ul>.*?<td data-title="Jackpot">\s*(.*?)\s*</td>',
                chunk, re.S,
            )
            if not rows:
                raise ValueError("Past Drawings 表格里没解析出任何行")

            fetched_at = dt.datetime.now(dt.timezone.utc).isoformat()
            parsed_rows = [(iso_date(date_raw), numbers_html, jackpot_raw) for date_raw, numbers_html, jackpot_raw in rows]
            latest_draw_date = max(d for d, _, _ in parsed_rows)
            next_draw_info = _parse_next_draw_info(html, latest_draw_date)

            results = []
            for draw_date, numbers_html, jackpot_raw in parsed_rows:
                numbers = [int(n) for n in re.findall(r"<li>(\d+)</li>", numbers_html)]
                jackpot_amount = None
                m = re.search(r"\$([\d,]+)", jackpot_raw)
                if m:
                    jackpot_amount = int(m.group(1).replace(",", ""))
                results.append(CashResult(
                    game="Idaho Cash",
                    draw_date=draw_date,
                    numbers=numbers,
                    jackpot_amount=jackpot_amount,
                    fetched_at=fetched_at,
                    source_url=URL,
                    next_draw_date=next_draw_info["next_draw_date"],
                    next_jackpot_amount=next_draw_info["next_jackpot_amount"],
                ))
            return results
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("第 %d 次尝试失败: %s", attempt, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SEC * attempt)
    raise RuntimeError("重试多次后仍然失败") from last_err


def validate(result: CashResult) -> None:
    if len(result.numbers) != 5:
        raise ValueError(f"主号码应为5个: {result.numbers}")
    if not all(1 <= n <= 45 for n in result.numbers):
        raise ValueError(f"号码超出 1-45 范围: {result.numbers}")


MAX_HISTORY = 100


def save_results(results: list[CashResult]) -> Path:
    out_dir = OUTPUT_DIR / "idaho" / "idaho-cash"
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

    new_entries = [r.to_dict() for r in results]
    new_keys = {e["draw_date"] for e in new_entries}
    history = [h for h in history if h.get("draw_date") not in new_keys]

    merged = new_entries + history
    merged.sort(key=lambda e: e["draw_date"], reverse=True)
    merged = merged[:MAX_HISTORY]

    out_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2))
    log.info("已保存: %s (%d 条记录，本次更新 %d 条)", out_path, len(merged), len(new_entries))
    return out_path


def main():
    results = fetch()
    for r in results:
        validate(r)
    log.info("抓取结果: %s", [r.to_dict() for r in results])
    save_results(results)


if __name__ == "__main__":
    main()
