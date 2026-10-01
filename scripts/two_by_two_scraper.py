"""
2by2（powerball.com/2by2）抓取

玩法本身跟系列里其它号码类玩法都不一样：选 2 个红球（1-26）+ 2 个白球
（1-26），奖金是固定金额（不滚动头奖）——"Top Prize $22,000"。页面文本流
里红球和白球之间没有任何文字标签分隔，只能按官方"How To Play"里描述的
顺序（"Select two red ball numbers... and two white ball numbers..."）
假设前 2 个是红球、后 2 个是白球；这是一个未经页面结构确认的假设，如果以后
发现号码归属弄反了，要回来改这里。

没有 Estimated Jackpot / Cash Value 这组字段（固定奖金，不用年金/现金二选
一），只有一个 "Top Prize" 金额。"Top Prize" 这个标签在整页只出现一次
（Winners 区块里中奖金额前面没有这个标签词，是直接把游戏名"2by2"当标题），
所以不需要像 Millionaire for Life 那样做位置消歧。
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
log = logging.getLogger("two_by_two_scraper")

URL = "https://www.powerball.com/2by2"
USER_AGENT = "LottoLiveApp/0.1 (+mailto:YOUR-CONTACT-EMAIL@example.com)"
REQUEST_TIMEOUT_SEC = 15
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 5
MAX_HISTORY = 100

OUTPUT_DIR = Path("data")

DATE_PATTERN = r"[A-Za-z]{3},\s*[A-Za-z]{3}\s+\d{1,2},\s*\d{4}"
STATES_PATTERN = r"(None|[A-Z]{2}(?:,\s*[A-Z]{2})*)"


@dataclasses.dataclass
class TwoByTwoResult:
    draw_date: Optional[str]
    red_balls: list[int]
    white_balls: list[int]
    next_drawing: dict  # {next_draw_date, top_prize}
    winners_by_tier: dict  # {"Top Prize": {"prize": "$22,000", "states": [...]}}
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


def parse_winning_numbers(text: str) -> tuple[Optional[str], list[int], list[int]]:
    pattern = re.compile(
        r"Winning Numbers\s*\n\s*"
        rf"({DATE_PATTERN})\s*\n\s*"
        r"(\d{1,2})\s*\n\s*"
        r"(\d{1,2})\s*\n\s*"
        r"(\d{1,2})\s*\n\s*"
        r"(\d{1,2})",
        re.IGNORECASE,
    )
    m = pattern.search(text)
    if not m:
        return None, [], []
    draw_date = m.group(1)
    red_balls = [int(m.group(2)), int(m.group(3))]
    white_balls = [int(m.group(4)), int(m.group(5))]
    return draw_date, red_balls, white_balls


def parse_next_drawing(text: str) -> dict:
    result: dict = {}
    date_m = re.search(rf"Next Drawing\s*\n\s*({DATE_PATTERN})", text, re.IGNORECASE)
    result["next_draw_date"] = date_m.group(1) if date_m else None

    prize_m = re.search(r"Top Prize\s*\n\s*(\$[\d,]+)", text, re.IGNORECASE)
    result["top_prize"] = prize_m.group(1).strip() if prize_m else None

    return result


def parse_winners_section(text: str) -> dict:
    pattern = re.compile(
        rf"Winners\s*\n\s*{DATE_PATTERN}\s*\n"
        r"2by2\s*\n"
        rf"(\$[\d,]+)\s*\n{STATES_PATTERN}",
        re.IGNORECASE,
    )
    m = pattern.search(text)
    if not m:
        return {}

    states_raw = m.group(2).strip()
    states = [] if states_raw == "None" else [s.strip() for s in states_raw.split(",")]
    return {"Top Prize": {"prize": m.group(1).strip(), "states": states}}


def validate(red_balls: list[int], white_balls: list[int]) -> None:
    if len(red_balls) != 2 or len(white_balls) != 2:
        raise ValueError(f"号码数量不对,抓到红球 {red_balls} 白球 {white_balls},预期各 2 个")
    if len(set(red_balls)) != 2:
        raise ValueError(f"红球有重复,号码不合法: {red_balls}")
    if len(set(white_balls)) != 2:
        raise ValueError(f"白球有重复,号码不合法: {white_balls}")
    if not all(1 <= n <= 26 for n in red_balls + white_balls):
        raise ValueError(f"号码超出 1-26 范围: 红球 {red_balls} 白球 {white_balls}")


def fetch() -> TwoByTwoResult:
    import requests

    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(URL, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_SEC)
            resp.raise_for_status()
            text = normalize_text(resp.text)

            draw_date, red_balls, white_balls = parse_winning_numbers(text)
            validate(red_balls, white_balls)

            return TwoByTwoResult(
                draw_date=draw_date,
                red_balls=red_balls,
                white_balls=white_balls,
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


def save_result(result: TwoByTwoResult) -> Path:
    out_dir = OUTPUT_DIR / "multistate" / "2by2"
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
