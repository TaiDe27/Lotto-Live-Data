"""Indiana Daily 4 抓取 — hoosierlottery.com。页面结构/SUPERBALL处理/session解析同
in_daily3_scraper.py，这里不重复。
"""

import dataclasses
import json
from pathlib import Path
from typing import Optional

import in_shared as shared

URL = "https://www.hoosierlottery.com/games/draw/daily-4/"
OUTPUT_DIR = Path("data")
DIGIT_COUNT = 4


@dataclasses.dataclass
class Daily4Result:
    game: str
    draw_date: str
    session: str
    digits: list[int]
    superball: Optional[int]
    fetched_at: str
    source_url: str

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def fetch() -> list[Daily4Result]:
    from bs4 import BeautifulSoup

    html = shared.fetch_html(URL)
    soup = BeautifulSoup(html, "html.parser")
    containers = [c for c in soup.select("div.numbers-container") if c.select("span.winning-number")]
    if not containers:
        raise ValueError("没找到任何开奖号码容器")

    results = []
    for container in containers:
        session_el = container.find_previous("span", class_="font-weight-bold")
        date_el = container.find_previous("span", class_="sub-title")
        if not session_el or not date_el:
            raise ValueError("没找到session标签或日期元素")
        raw_session = session_el.get_text(strip=True).lower()
        session = "midday" if raw_session == "midday" else "evening"
        draw_date = shared.iso_date(date_el.get_text(strip=True))

        spans = container.select("span.winning-number")
        if spans and "bonus-number" in (spans[-1].get("class") or []):
            main_spans, superball = spans[:-1], int(spans[-1].get_text(strip=True))
        else:
            main_spans, superball = spans, None
        digits = [int(s.get_text(strip=True)) for s in main_spans]

        results.append(Daily4Result(
            game="Daily 4",
            draw_date=draw_date,
            session=session,
            digits=digits,
            superball=superball,
            fetched_at=shared.fetched_at_now(),
            source_url=URL,
        ))
    return results


def validate(result: Daily4Result) -> None:
    if len(result.digits) != DIGIT_COUNT:
        raise ValueError(f"[{result.session}] 位数不对，抓到 {len(result.digits)} 位，预期 {DIGIT_COUNT} 位: {result.digits}")
    if not all(0 <= d <= 9 for d in result.digits):
        raise ValueError(f"[{result.session}] 数字超出 0-9 范围: {result.digits}")


def save_results(results: list[Daily4Result]) -> Path:
    out_dir = OUTPUT_DIR / "indiana" / "daily4"
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
    new_keys = {(e["draw_date"], e["session"]) for e in new_entries}
    history = [h for h in history if (h.get("draw_date"), h.get("session")) not in new_keys]

    merged = new_entries + history
    merged = merged[: shared.MAX_HISTORY]

    out_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2))
    shared.log.info("已保存: %s (%d 条记录，本次更新 %d 条)", out_path, len(merged), len(new_entries))
    return out_path


def main():
    results = fetch()
    for r in results:
        validate(r)
    shared.log.info("抓取结果: %s", [r.to_dict() for r in results])
    save_results(results)


if __name__ == "__main__":
    main()
