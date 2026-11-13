"""Indiana Hoosier Lotto (+ +PLUS) 抓取 — hoosierlottery.com。合规说明见 in_shared.py。

页面 /games/draw/hoosier-lotto/ 上同时有两组开奖号码：第一个
<div class="numbers-container">是Hoosier Lotto主开奖(6个号码，1-46，滚存
jackpot)，紧接着第二个是+PLUS(同一天同一批号码池里再开一次，6个号码，固定
$1,000,000头奖，不滚存)——这是两个独立的Game Entry(in-hoosier-lotto /
in-hoosier-lotto-plus)，但只需要一次HTTP请求就能拿到两组数据，所以这个脚本
一次抓取后分别存进两个latest.json文件。

只能抓到"最新一期"(没有历史翻页/POST表单支持)，跟其它Indiana脚本一样靠
Scheduler.gs重复轮询、每次append一条，积累到MAX_HISTORY。
"""

import dataclasses
import json
import re
import time
from pathlib import Path
from typing import Optional

import in_shared as shared

URL = "https://www.hoosierlottery.com/games/draw/hoosier-lotto/"
OUTPUT_DIR = Path("data")


@dataclasses.dataclass
class HoosierLottoResult:
    game: str
    draw_date: str
    numbers: list[int]
    jackpot_amount: Optional[str]
    fetched_at: str
    source_url: str
    next_draw_date: Optional[str] = None
    next_jackpot_amount: Optional[object] = None

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def _find_plus_fixed_prize(soup) -> Optional[int]:
    """+PLUS 是固定 $1,000,000 头奖，不滚存——不是从某个"滚存jackpot"数字抓来的，
    是直接从页面明文营销文字"Add +PLUS for a chance to win $1,000,000"里按$金额
    动态抓的（不是在Python里硬编码1000000），如果官方以后改了这个金额，这里也会
    跟着变，不是写死的假设。"""
    text_el = soup.find(string=re.compile(r"chance to win \$[\d,]+"))
    if not text_el:
        return None
    m = re.search(r"chance to win \$([\d,]+)", text_el)
    return int(m.group(1).replace(",", "")) if m else None


def fetch() -> tuple[HoosierLottoResult, HoosierLottoResult]:
    from bs4 import BeautifulSoup

    html = shared.fetch_html(URL)
    soup = BeautifulSoup(html, "html.parser")
    containers = [c for c in soup.select("div.numbers-container") if c.select("span.winning-number")]
    if len(containers) < 2:
        raise ValueError(f"预期至少2组开奖号码(主开奖+ +PLUS)，只找到 {len(containers)} 组")

    # Site-wide jackpot ticker only carries the real rolling-jackpot game (Hoosier Lotto), not
    # the fixed-prize +PLUS add-on drawing — see in_shared.find_jackpot_amount's docstring.
    jackpot_amount = shared.find_jackpot_amount(soup, "hoosier-lotto")
    plus_fixed_prize = _find_plus_fixed_prize(soup)

    def parse_container(container, game_name: str, jackpot: Optional[str], next_jackpot: Optional[object], next_draw_date: Optional[str]) -> HoosierLottoResult:
        date_el = container.find_previous("span", class_="sub-title")
        if not date_el:
            raise ValueError(f"{game_name}: 没找到日期元素")
        draw_date = shared.iso_date(date_el.get_text(strip=True))
        # Defensive guard (2026-10-23, no observed incident) - Hoosier Lotto and +PLUS come from
        # the same simultaneous Wed/Sat draw so there's no day/evening-style gap between them, but
        # skip non-numeric placeholder text here too rather than crash, matching every other
        # scraper's fix this round - validate() below already turns a short/empty list into a
        # clean error instead of a raw int() traceback.
        numbers = [int(t) for s in container.select("span.winning-number") if (t := s.get_text(strip=True)).isdigit()]
        return HoosierLottoResult(
            game=game_name,
            draw_date=draw_date,
            numbers=numbers,
            jackpot_amount=jackpot,
            fetched_at=shared.fetched_at_now(),
            source_url=URL,
            next_draw_date=next_draw_date,
            next_jackpot_amount=next_jackpot,
        )

    main = parse_container(containers[0], "Hoosier Lotto", jackpot_amount, None, None)
    # Hero header (.jackpot-amount/.drawing-date) on this page belongs to Hoosier Lotto (the
    # main game this page is titled for) — resolved against Hoosier Lotto's own just-scraped
    # draw_date before being reused for +PLUS below (same simultaneous draw, same next date).
    next_draw_info = shared.find_next_draw_info(soup, main.draw_date)
    main.next_draw_date = next_draw_info["next_draw_date"]
    main.next_jackpot_amount = next_draw_info["next_jackpot_amount"]
    plus = parse_container(containers[1], "Hoosier Lotto +PLUS", None, plus_fixed_prize, next_draw_info["next_draw_date"])
    return main, plus


def validate(result: HoosierLottoResult) -> None:
    if len(result.numbers) != 6:
        raise ValueError(f"{result.game}: 号码数不对，抓到 {len(result.numbers)} 个，预期 6 个: {result.numbers}")
    if not all(1 <= n <= 46 for n in result.numbers):
        raise ValueError(f"{result.game}: 号码超出 1-46 范围: {result.numbers}")


def save_result(result: HoosierLottoResult, subdir: str) -> Path:
    out_dir = OUTPUT_DIR / "indiana" / subdir
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

    history = [h for h in history if h.get("draw_date") != result.draw_date]
    merged = [result.to_dict()] + history
    merged = merged[: shared.MAX_HISTORY]

    out_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2))
    shared.log.info("已保存: %s (%d 条记录)", out_path, len(merged))
    return out_path


def main():
    main_result, plus_result = fetch()
    validate(main_result)
    validate(plus_result)
    shared.log.info("抓取结果: %s | %s", main_result.to_dict(), plus_result.to_dict())
    save_result(main_result, "hoosier-lotto")
    save_result(plus_result, "hoosier-lotto-plus")


if __name__ == "__main__":
    main()
