"""Indiana Quick Draw 抓取 — hoosierlottery.com。合规说明见 in_shared.py。

页面 /games/draw/quick-draw/ 只展示"最新一期"(不区分URL，-evening变体展示的是同一份
"最新"数据)，但页面上确实带了真实的session文字标签("Midday"/"Evening")，跟
in_daily3_scraper.py读session标签的方式一样可靠——这个脚本按两个不同的cron时间点
(约1:20pm/11pm ET之后)各跑一次，每次信任页面自己打印的session标签，而不是假设
"这次跑就一定是某个session"。

80选10，开20个号。号码容器里最后一个<span>带额外的"bonus-number" class，是单独
一个加购的BULLSEYE号——它恰好总是20个里的某一个被高亮，不是额外抽的号。跟Illinois
Extra Shot一样先只存不接入对奖逻辑。
"""

import dataclasses
import json
from pathlib import Path
from typing import Optional

import in_shared as shared

URL = "https://www.hoosierlottery.com/games/draw/quick-draw/"
OUTPUT_DIR = Path("data")
MAIN_COUNT = 20


@dataclasses.dataclass
class QuickDrawResult:
    game: str
    draw_date: str
    session: str
    numbers: list[int]
    # Named "bonus_number" (not "bullseye") on purpose: this lets the Swift app side reuse its
    # existing ScrapedSessionTextJackpotPoolResult/fetchLiveSessionTextJackpotPoolHistory helper
    # (same draw_date/session/numbers/bonus_number/jackpot_amount shape already used by IL Lotto's
    # Extra Shot etc.) instead of writing a new hardcoded mapper from scratch.
    bonus_number: Optional[int]
    fetched_at: str
    source_url: str

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def fetch() -> QuickDrawResult:
    from bs4 import BeautifulSoup

    html = shared.fetch_html(URL)
    soup = BeautifulSoup(html, "html.parser")
    container = next((c for c in soup.select("div.numbers-container") if c.select("span.winning-number")), None)
    if container is None:
        raise ValueError("没找到开奖号码容器")

    session_el = container.find_previous("span", class_="font-weight-bold")
    date_el = container.find_previous("span", class_="sub-title")
    if not session_el or not date_el:
        raise ValueError("没找到session标签或日期元素")
    raw_session = session_el.get_text(strip=True).lower()
    session = "midday" if raw_session == "midday" else "evening"
    draw_date = shared.iso_date(date_el.get_text(strip=True))

    spans = container.select("span.winning-number")
    bullseye_span = next((s for s in spans if "bonus-number" in (s.get("class") or [])), None)
    main_spans = [s for s in spans if s is not bullseye_span]
    numbers = [int(s.get_text(strip=True)) for s in main_spans]
    bullseye = int(bullseye_span.get_text(strip=True)) if bullseye_span else None

    return QuickDrawResult(
        game="Quick Draw",
        draw_date=draw_date,
        session=session,
        numbers=numbers,
        bonus_number=bullseye,
        fetched_at=shared.fetched_at_now(),
        source_url=URL,
    )


def validate(result: QuickDrawResult) -> None:
    if len(result.numbers) != MAIN_COUNT:
        raise ValueError(f"号码数不对，抓到 {len(result.numbers)} 个，预期 {MAIN_COUNT} 个: {result.numbers}")
    if not all(1 <= n <= 80 for n in result.numbers):
        raise ValueError(f"号码超出 1-80 范围: {result.numbers}")
    if result.bonus_number is not None and not (1 <= result.bonus_number <= 80):
        raise ValueError(f"BULLSEYE 号超出 1-80 范围: {result.bonus_number}")


def save_result(result: QuickDrawResult) -> Path:
    out_dir = OUTPUT_DIR / "indiana" / "quickdraw"
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

    history = [h for h in history if (h.get("draw_date"), h.get("session")) != (result.draw_date, result.session)]
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
