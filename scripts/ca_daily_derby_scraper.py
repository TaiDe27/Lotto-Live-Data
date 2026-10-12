"""
California Daily Derby 抓取 — calottery.com 官方站直接抓

合规说明、UA选择理由同 ca_superlotto_plus_scraper.py，这里不重复。

Daily Derby 是赛马风格游戏，不是数字/球号玩法：开奖结果是"第一/二/三名是几号马、
叫什么名字"+"比赛用时"。App 那边现在把这个当"结果展示"接了进去（不做选号/对奖/
Pick&Buy/OCR，只在彩种卡片上原样显示开奖结果，参照California Lottery官方app的卡片
布局）——`derby_positions`是从`first_place`/`second_place`/`third_place`里提取出的
纯马号([1st, 2nd, 3rd]三个int)，是App那边实际会读取的字段；原始的"马号 - 马名"字
符串继续保留，留作调试/以后万一要展示马名用。

`grand_prize_winners`/`grand_prize_amount`是从`tiers`列表里"Grand Prize"那一档copy
上来的两个顶层字段——跟ca_superlotto_plus_scraper.py/ca_fantasy5_scraper.py处理
jackpot_amount的理由一样：App那边`LiveMappingRule`的字段映射只能走dot-path，够不到
数组元素内部，所以在抓取脚本这一层就把需要的值提到顶层，而不是去扩展App那边的
解析器语法。

`next_jackpot_estimate`抓自页面顶部"Get on track to win $X*"的推广banner(*Estimated
Grand Prize)。`next_draw_date`不是页面上单独给的字段，是本脚本自己算的：Daily Derby
官方页面原文写着"there are drawings every day"，所以直接用draw_date+1天。
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
log = logging.getLogger("ca_daily_derby_scraper")

URL = "https://www.calottery.com/en/draw-games/daily-derby"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
REQUEST_TIMEOUT_SEC = 15
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 5

OUTPUT_DIR = Path("data")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def iso_date(raw: str) -> str:
    """"THU/OCT 8, 2026" -> "2026-10-08". See arkansas/california scrapers' own iso_date()."""
    return dt.datetime.strptime(raw.strip().title(), "%a/%b %d, %Y").strftime("%Y-%m-%d")


def horse_number(place_text: str) -> Optional[int]:
    """"07 - Eureka" -> 7. Returns None rather than raising if the leading token isn't numeric."""
    m = re.match(r"\s*(\d+)", place_text)
    return int(m.group(1)) if m else None


@dataclasses.dataclass
class TierResult:
    label: str
    winners: int
    prize: str


@dataclasses.dataclass
class DailyDerbyResult:
    game: str
    draw_date: str
    draw_number: Optional[str]
    first_place: str  # 原样保留，如 "03 - Hot Shot"
    second_place: str
    third_place: str
    derby_positions: list[int]  # [first, second, third] 纯马号，App实际读取的字段
    race_time: Optional[str]  # 如 "1:43.00"
    tiers: list[dict]
    grand_prize_winners: int
    grand_prize_amount: Optional[str]
    next_draw_date: str
    next_jackpot_estimate: Optional[str]
    fetched_at: str
    source_url: str

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def fetch() -> DailyDerbyResult:
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
            draw_date_raw = date_el.get_text(strip=True) if date_el else None
            draw_date = iso_date(draw_date_raw) if draw_date_raw else None
            num_el = card.select_one(".draw-cards--draw-number")
            draw_number = num_el.get_text(strip=True).replace("Draw #", "").strip() if num_el else None

            places = {"first": None, "second": None, "third": None}
            race_time = None
            for li in card.select("ul.draw-cards--derby-winners li"):
                text = re.sub(r"\s+", " ", li.get_text(strip=True))
                m = re.match(r"(First|Second|Third):\s*(.+)", text, re.I)
                if m:
                    places[m.group(1).lower()] = m.group(2).strip()
                    continue
                m2 = re.match(r"Race Time:\s*(.+)", text, re.I)
                if m2:
                    race_time = m2.group(1).strip()

            tiers = []
            table = soup.select_one("table.table-last-draw")
            if table:
                for row in table.select("tbody tr"):
                    if "total" in row.get("class", []):
                        continue
                    cells = [c.get_text(strip=True) for c in row.select("td")]
                    if len(cells) != 3:
                        continue
                    label, winners_text, prize = cells
                    winners = int(re.sub(r"[^\d]", "", winners_text) or 0)
                    tiers.append(dataclasses.asdict(TierResult(label=label, winners=winners, prize=prize)))

            if not draw_date or not all(places.values()):
                raise ValueError(f"解析出的数据不完整: draw_date={draw_date} places={places}")

            derby_positions = [horse_number(places["first"]), horse_number(places["second"]), horse_number(places["third"])]
            if any(p is None for p in derby_positions):
                raise ValueError(f"名次号码解析失败: {places}")

            grand_prize = next((t for t in tiers if t["label"] == "Grand Prize"), None)
            grand_prize_winners = grand_prize["winners"] if grand_prize else 0
            grand_prize_amount = grand_prize["prize"] if grand_prize else None

            next_draw_date = (dt.datetime.strptime(draw_date, "%Y-%m-%d") + dt.timedelta(days=1)).strftime("%Y-%m-%d")

            next_jackpot_estimate = None
            hero = soup.select_one("h2.hero__drawgames--heading span[lang='en']")
            if hero:
                next_jackpot_estimate = hero.get_text(strip=True)

            return DailyDerbyResult(
                game="Daily Derby",
                draw_date=draw_date,
                draw_number=draw_number,
                first_place=places["first"],
                second_place=places["second"],
                third_place=places["third"],
                derby_positions=derby_positions,
                race_time=race_time,
                tiers=tiers,
                grand_prize_winners=grand_prize_winners,
                grand_prize_amount=grand_prize_amount,
                next_draw_date=next_draw_date,
                next_jackpot_estimate=next_jackpot_estimate,
                fetched_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                source_url=URL,
            )
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("第 %d 次尝试失败: %s", attempt, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SEC * attempt)
    raise RuntimeError("重试多次后仍然失败") from last_err


def validate(result: DailyDerbyResult) -> None:
    if not (result.first_place and result.second_place and result.third_place):
        raise ValueError(f"名次信息不完整: {result.first_place} / {result.second_place} / {result.third_place}")
    if len(result.derby_positions) != 3:
        raise ValueError(f"derby_positions 数量不对: {result.derby_positions}")


MAX_HISTORY = 100


def save_results(result: DailyDerbyResult) -> Path:
    out_dir = OUTPUT_DIR / "california" / "daily-derby"
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
