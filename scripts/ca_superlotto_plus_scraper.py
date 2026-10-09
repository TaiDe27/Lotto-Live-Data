"""
California SuperLotto Plus 抓取 — calottery.com 官方站直接抓

合规说明：calottery.com 没有 Cloudflare 防护，Terms of Service 里没有明确
禁止 robot/spider/数据抓取的条款（唯一相关的一条是"不得绕过 robot exclusion
header"，即要遵守 robots.txt），而 robots.txt 本身并没有禁止
/en/draw-games/ 这类开奖结果页面。这个脚本只抓事实性开奖数据（号码、日期、
奖级人数/金额），不保存整页HTML、不转载站点文案。

页面是服务端直接渲染的静态HTML（Sitecore CMS），用 requests 就够。
主号码5个(1-47) + 一个独立号池的 Mega 号(1-27，官方站自己标"Superball"，
玩家惯用叫法是 Mega/SuperLotto Plus 的"Mega number"——沿用 App 现有
games.json 里 specialName="Mega" 的叫法)。

页面只展示最新一期(没有历史列表)，所以这份脚本每次只拿一条，靠
save_results() 的 merge-dedupe 逻辑在 latest.json 里逐步累积历史——跟
az_the_pick_scraper.py 等"官方API一次给一批历史"的脚本不一样，这里更像是
"定时拍快照"，必须确保 Scheduler.gs 在官方实际开奖后有抓到，不然那一期就
永久漏掉了（没有"回头补抓"的来源）。

同时解析页面上自带的"Prize breakdown"表格(Matching Numbers / Winning
Tickets / Prize Amounts)，拿到各奖级的中奖人数和奖金——这个信息比其他
scraper拿到的还全(Powerball系列都没有这么细的每期奖级人数)。
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
log = logging.getLogger("ca_superlotto_plus_scraper")

def iso_date(raw: str) -> str:
    """"WED/OCT 7, 2026" -> "2026-10-07" — calottery.com's own date text, normalized to the
    same ISO yyyy-MM-dd shape every other scraper in this repo already emits."""
    import datetime as _dt
    return _dt.datetime.strptime(raw, "%a/%b %d, %Y").strftime("%Y-%m-%d")

URL = "https://www.calottery.com/en/draw-games/superlotto-plus"
# calottery.com 对这份代码库其他脚本一直用的"诚实身份"UA
# (LottoLiveApp/0.1 ...) 做了过滤——确认过是 200 状态码但返回一个假的
# "Service Unavailable"维护页面，不是真的服务不可用；换成普通浏览器UA就能
# 拿到真实内容。条款没禁止爬虫、robots.txt也放行这些页面，只是这一关UA检查
# 需要绕过，跟对抗Cloudflare那种专门的反爬虫验证不是一回事——这个决定是
# 跟用户确认过的，仅对 calottery.com 这几个脚本生效，不代表要改其他站点的
# 爬虫身份策略。
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
REQUEST_TIMEOUT_SEC = 15
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 5

OUTPUT_DIR = Path("data")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


@dataclasses.dataclass
class TierResult:
    label: str  # 原样保留页面文案，如 "Matched 5 of 5 numbers"
    winners: int
    prize: str  # 原样保留，如 "$156,839" / "Free play"（有些低等级是免费票不是钱）


@dataclasses.dataclass
class SuperLottoPlusResult:
    game: str
    draw_date: str  # "WED/OCT 7, 2026" 原样保留
    draw_number: Optional[str]
    main_numbers: list[int]  # 5个
    mega_number: Optional[int]
    jackpot_amount: Optional[str]  # 奖级表第一行("5 + Mega")的奖金，原样保留如"$61,000,000"
    tiers: list[dict]
    fetched_at: str
    source_url: str

    def to_dict(self) -> dict:
        d = dataclasses.asdict(self)
        return d


def fetch() -> SuperLottoPlusResult:
    import requests
    from bs4 import BeautifulSoup

    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(URL, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_SEC)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")

            card = soup.select_one("[id^='winningNumbers']")
            if not card:
                raise ValueError("页面里没找到 winningNumbers 开奖卡片")

            date_el = card.select_one(".draw-cards--draw-date")
            draw_date = iso_date(date_el.get_text(strip=True)) if date_el else None
            num_el = card.select_one(".draw-cards--draw-number")
            draw_number = num_el.get_text(strip=True).replace("Draw #", "") if num_el else None

            balls = card.select("li.list-inline-item")
            main_numbers, mega_number = [], None
            for li in balls:
                span = li.select_one("span.draw-cards--winning-numbers-inner-wrapper")
                if span is None:
                    continue
                value = int(span.get_text(strip=True))
                if "list-inline-item--special" in li.get("class", []):
                    mega_number = value
                else:
                    main_numbers.append(value)

            tiers = []
            table = soup.select_one("table.table-last-draw")
            if table:
                for row in table.select("tbody tr"):
                    if "total" in row.get("class", []):
                        continue  # 跳过合计行(<tr class="total">，label是"Total Winning Tickets")
                    cells = [c.get_text(strip=True) for c in row.select("td")]
                    if len(cells) != 3:
                        continue
                    label, winners_text, prize = cells
                    winners = int(re.sub(r"[^\d]", "", winners_text) or 0)
                    tiers.append(dataclasses.asdict(TierResult(label=label, winners=winners, prize=prize)))

            jackpot_amount = tiers[0]["prize"] if tiers else None

            if not main_numbers or not draw_date:
                raise ValueError(f"解析出的数据不完整: main_numbers={main_numbers} draw_date={draw_date}")

            return SuperLottoPlusResult(
                game="SuperLotto Plus",
                draw_date=draw_date,
                draw_number=draw_number,
                main_numbers=main_numbers,
                mega_number=mega_number,
                jackpot_amount=jackpot_amount,
                tiers=tiers,
                fetched_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                source_url=URL,
            )
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("第 %d 次尝试失败: %s", attempt, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SEC * attempt)
    raise RuntimeError("重试多次后仍然失败") from last_err


def validate(result: SuperLottoPlusResult) -> None:
    if len(result.main_numbers) != 5:
        raise ValueError(f"主号码应为5个，抓到 {len(result.main_numbers)} 个: {result.main_numbers}")
    if result.mega_number is None:
        raise ValueError("没抓到 Mega 号码")
    if not all(1 <= n <= 47 for n in result.main_numbers):
        raise ValueError(f"主号码超出 1-47 范围: {result.main_numbers}")
    if not (1 <= result.mega_number <= 27):
        raise ValueError(f"Mega 号码超出 1-27 范围: {result.mega_number}")


MAX_HISTORY = 100


def save_results(result: SuperLottoPlusResult) -> Path:
    out_dir = OUTPUT_DIR / "california" / "superlotto-plus"
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
