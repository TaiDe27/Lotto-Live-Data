"""
Powerball Double Play 抓取 — powerball.com/double-play

跟主 Powerball 抓取(powerball_scraper.py)共用同一套"标签词 + 正则"解析
逻辑,因为用户截图确认了这个页面的 Winning Numbers 区块结构跟主页几乎
一样(日期 + 6个数字,后面是 View Results / Check Your Numbers 按钮)。

已用真实抓取文本核对过(2026-10-01):号码、Top Prize、Winners 区块三部分
都验证通过。Winners 区块的 tier 名字跟主 Powerball 页面不一样("Double
Play" / "Match 5"，不是"Match 5 + Power Play"这种)，所以单独写了一个
`parse_winners_section`，不是复用主脚本那个按"Match N + Power Play"
措辞写的版本。
"""

import dataclasses
import datetime as dt
import json
import logging
import re
import time
from pathlib import Path
from typing import Optional

import sys
sys.path.insert(0, str(Path(__file__).parent))
from powerball_scraper import (  # noqa: E402  复用主脚本已验证过的函数
    normalize_text,
    parse_winning_numbers,
    parse_next_drawing,
    validate,
    DATE_PATTERN,
    USER_AGENT,
    REQUEST_TIMEOUT_SEC,
    MAX_RETRIES,
    RETRY_BACKOFF_SEC,
)

STATES_PATTERN = r"(None|[A-Z]{2}(?:,\s*[A-Z]{2})*)"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("powerball_double_play_scraper")

URL = "https://www.powerball.com/double-play"
OUTPUT_DIR = Path("data")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


@dataclasses.dataclass
class DoublePlayResult:
    game: str
    draw_date: Optional[str]
    white_balls: list[int]
    red_ball: Optional[int]
    next_drawing: dict
    winners_by_tier: dict
    fetched_at: str
    source_url: str

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def parse_winners_section(text: str) -> dict:
    """'Double Play\\n$10 Million Winners\\nNone\\nMatch 5\\n$500,000 Winners\\nNone' 这种结构——
    跟主 Powerball 页面"Match 5 + Power Play"式的 tier 措辞不一样，不能复用主脚本那个函数。"""
    pattern = re.compile(
        rf"Winners\s*\n\s*{DATE_PATTERN}\s*\n"
        r"Double Play\s*\n"
        rf"\$[\d,]+(?:\.\d+)?\s*(?:Million|Billion)?\s*Winners\s*\n{STATES_PATTERN}\s*\n"
        rf"Match 5\s*\n\$[\d,]+(?:\.\d+)?\s*(?:Million|Billion)?\s*Winners\s*\n{STATES_PATTERN}",
        re.IGNORECASE,
    )
    m = pattern.search(text)
    if not m:
        return {}

    def states_list(raw: str) -> list[str]:
        raw = raw.strip()
        return [] if raw == "None" else [s.strip() for s in raw.split(",")]

    return {
        "Double Play JACKPOT": {"states": states_list(m.group(1))},
        "Match 5": {"states": states_list(m.group(2))},
    }


def fetch() -> DoublePlayResult:
    import requests

    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(URL, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_SEC)
            resp.raise_for_status()
            text = normalize_text(resp.text)

            draw_date, white_balls, red_ball, _power_play = parse_winning_numbers(text)
            # Double Play 没有 Power Play,_power_play 理论上应该是 None;
            # 如果不是 None,说明页面结构比预期复杂,建议报出来看一眼
            if _power_play is not None:
                log.warning(
                    "Double Play 页面竟然抓到了 Power Play 倍数(%s),"
                    "跟预期结构不符,建议核对页面是否变了", _power_play
                )
            validate(white_balls, red_ball)

            next_drawing = parse_next_drawing(text)
            winners = parse_winners_section(text)

            return DoublePlayResult(
                game="Powerball Double Play",
                draw_date=draw_date,
                white_balls=white_balls,
                red_ball=red_ball,
                next_drawing=next_drawing,
                winners_by_tier=winners,
                fetched_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                source_url=URL,
            )
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("第 %d 次尝试失败: %s", attempt, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SEC * attempt)
    raise RuntimeError(f"重试 {MAX_RETRIES} 次后仍然失败") from last_err


MAX_HISTORY = 100


def save_result(result: DoublePlayResult) -> Path:
    """同 powerball_scraper.save_result:只维护一份 latest.json 数组,最新
    在最前,按 draw_date 去重,最多 MAX_HISTORY 条,不再按日期单独存文件。"""
    out_dir = OUTPUT_DIR / "multistate" / "powerball-double-play"
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
