"""Indiana CA$H 5 抓取 — hoosierlottery.com。合规说明见 in_shared.py。

5个号码从1-45抽出，没有bonus号。一天一期(夜场)，jackpot滚存，取自站点共享的
"Current Jackpots"滚动条(见 in_shared.find_jackpot_amount)。只能抓到最新一期
(没有历史翻页)。
"""

import dataclasses
import json
from pathlib import Path
from typing import Optional

import in_shared as shared

URL = "https://www.hoosierlottery.com/games/draw/cash-5/"
OUTPUT_DIR = Path("data")


@dataclasses.dataclass
class Cash5Result:
    game: str
    draw_date: str
    numbers: list[int]
    jackpot_amount: Optional[str]
    fetched_at: str
    source_url: str

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def fetch() -> Cash5Result:
    from bs4 import BeautifulSoup

    html = shared.fetch_html(URL)
    soup = BeautifulSoup(html, "html.parser")
    container = next((c for c in soup.select("div.numbers-container") if c.select("span.winning-number")), None)
    if container is None:
        raise ValueError("没找到开奖号码容器")

    date_el = container.find_previous("span", class_="sub-title")
    if not date_el:
        raise ValueError("没找到日期元素")
    draw_date = shared.iso_date(date_el.get_text(strip=True))
    numbers = [int(s.get_text(strip=True)) for s in container.select("span.winning-number")]
    jackpot_amount = shared.find_jackpot_amount(soup, "cash-5")

    return Cash5Result(
        game="CA$H 5",
        draw_date=draw_date,
        numbers=numbers,
        jackpot_amount=jackpot_amount,
        fetched_at=shared.fetched_at_now(),
        source_url=URL,
    )


def validate(result: Cash5Result) -> None:
    if len(result.numbers) != 5:
        raise ValueError(f"号码数不对，抓到 {len(result.numbers)} 个，预期 5 个: {result.numbers}")
    if not all(1 <= n <= 45 for n in result.numbers):
        raise ValueError(f"号码超出 1-45 范围: {result.numbers}")


def save_result(result: Cash5Result) -> Path:
    out_dir = OUTPUT_DIR / "indiana" / "cash5"
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
    result = fetch()
    validate(result)
    shared.log.info("抓取结果: %s", result.to_dict())
    save_result(result)


if __name__ == "__main__":
    main()
