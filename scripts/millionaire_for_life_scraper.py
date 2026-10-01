"""
Millionaire for Life（powerball.com/millionaire-for-life）抓取

跟 powerball_scraper.py 同一套思路：在纯文本流上按固定标签词做正则匹配。
这个玩法选 5 个白球（1-58）+ 1 个 Millionaire Ball（1-5），没有倍投
（Power Play 那种）玩法。奖金结构是固定年金（不滚动的头奖），"Top Prize"
标签在页面里出现两次——一次在 Next Drawing 区块（后面紧跟 "Cash Option"），
一次在 Winners 区块（后面紧跟中奖州份）——靠各自紧邻的下一个标签词区分，
不靠出现顺序猜测。
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
log = logging.getLogger("millionaire_for_life_scraper")

URL = "https://www.powerball.com/millionaire-for-life"
USER_AGENT = "LottoLiveApp/0.1 (+mailto:YOUR-CONTACT-EMAIL@example.com)"
REQUEST_TIMEOUT_SEC = 15
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 5
MAX_HISTORY = 100

OUTPUT_DIR = Path("data")

DATE_PATTERN = r"[A-Za-z]{3},\s*[A-Za-z]{3}\s+\d{1,2},\s*\d{4}"
MONEY_PATTERN = r"\$?([\d,]+(?:\.\d+)?\s*(?:Million|Billion|million|billion))"
STATES_PATTERN = r"(None|[A-Z]{2}(?:,\s*[A-Z]{2})*)"


@dataclasses.dataclass
class MillionaireForLifeResult:
    draw_date: Optional[str]
    white_balls: list[int]
    millionaire_ball: Optional[int]
    next_drawing: dict  # {next_draw_date, top_prize, cash_option}
    winners_by_tier: dict  # {"Top Prize": {"prize": "...", "states": [...]}, "Match 5": {...}}
    fetched_at: str
    source_url: str

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def normalize_text(html: str) -> str:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n")
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    return "\n".join(lines)


def parse_winning_numbers(text: str) -> tuple[Optional[str], list[int], Optional[int]]:
    pattern = re.compile(
        r"Winning Numbers\s*\n\s*"
        rf"({DATE_PATTERN})\s*\n\s*"
        r"(\d{1,2})\s*\n\s*"
        r"(\d{1,2})\s*\n\s*"
        r"(\d{1,2})\s*\n\s*"
        r"(\d{1,2})\s*\n\s*"
        r"(\d{1,2})\s*\n\s*"
        r"(\d)",
        re.IGNORECASE,
    )
    m = pattern.search(text)
    if not m:
        return None, [], None
    draw_date = m.group(1)
    white_balls = [int(m.group(i)) for i in range(2, 7)]
    millionaire_ball = int(m.group(7))
    return draw_date, white_balls, millionaire_ball


def parse_next_drawing(text: str) -> dict:
    result: dict = {}
    date_m = re.search(rf"Next Drawing\s*\n\s*({DATE_PATTERN})", text, re.IGNORECASE)
    result["next_draw_date"] = date_m.group(1) if date_m else None

    top_prize_m = re.search(r"Top Prize\s*\n(.+?)\s*\nCash Option", text, re.IGNORECASE)
    result["top_prize"] = top_prize_m.group(1).strip() if top_prize_m else None

    cash_m = re.search(rf"Cash Option\s*\n\s*{MONEY_PATTERN}", text, re.IGNORECASE)
    result["cash_option"] = cash_m.group(1).strip() if cash_m else None

    return result


def parse_winners_section(text: str) -> dict:
    pattern = re.compile(
        rf"Winners\s*\n\s*{DATE_PATTERN}\s*\n"
        rf"Top Prize\s*\n(.+?)\s*\n{STATES_PATTERN}\s*\n"
        rf"Match 5\s*\n(.+?)\s*\n{STATES_PATTERN}",
        re.IGNORECASE,
    )
    m = pattern.search(text)
    if not m:
        return {}

    def states_list(raw: str) -> list[str]:
        raw = raw.strip()
        return [] if raw == "None" else [s.strip() for s in raw.split(",")]

    return {
        "Top Prize": {"prize": m.group(1).strip(), "states": states_list(m.group(2))},
        "Match 5": {"prize": m.group(3).strip(), "states": states_list(m.group(4))},
    }


def validate(white_balls: list[int], millionaire_ball: Optional[int]) -> None:
    if len(white_balls) != 5:
        raise ValueError(f"白球数量不对,抓到 {len(white_balls)} 个,预期 5 个: {white_balls}")
    if len(set(white_balls)) != 5:
        raise ValueError(f"白球有重复,号码不合法: {white_balls}")
    if not all(1 <= n <= 58 for n in white_balls):
        raise ValueError(f"白球超出 1-58 范围: {white_balls}")
    if millionaire_ball is None:
        raise ValueError("没抓到 Millionaire Ball 号码")
    if not (1 <= millionaire_ball <= 5):
        raise ValueError(f"Millionaire Ball 超出 1-5 范围: {millionaire_ball}")


def fetch() -> MillionaireForLifeResult:
    import requests

    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(URL, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_SEC)
            resp.raise_for_status()
            text = normalize_text(resp.text)

            draw_date, white_balls, millionaire_ball = parse_winning_numbers(text)
            validate(white_balls, millionaire_ball)

            return MillionaireForLifeResult(
                draw_date=draw_date,
                white_balls=white_balls,
                millionaire_ball=millionaire_ball,
                next_drawing=parse_next_drawing(text),
                winners_by_tier=parse_winners_section(text),
                fetched_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                source_url=URL,
            )
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("第 %d 次尝试失败: %s", attempt, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SEC * attempt)
    raise RuntimeError(f"重试 {MAX_RETRIES} 次后仍然失败") from last_err


def save_result(result: MillionaireForLifeResult) -> Path:
    out_dir = OUTPUT_DIR / "multistate" / "millionaire-for-life"
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
    if history and history[0].get("draw_date") == new_entry.get("draw_date"):
        history[0] = new_entry
    else:
        history.insert(0, new_entry)
    history = history[:MAX_HISTORY]

    out_path.write_text(json.dumps(history, ensure_ascii=False, indent=2))
    log.info("已保存: %s (%d 条记录)", out_path, len(history))
    return out_path


def main():
    result = fetch()
    log.info("抓取结果: %s", result.to_dict())
    save_result(result)


if __name__ == "__main__":
    main()
