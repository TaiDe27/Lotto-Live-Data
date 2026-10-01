"""
Lotto America（powerball.com/lotto-america）抓取

5 个红球（1-52）+ 1 个 Star Ball（1-10），外加可选的 "All Star Bonus"
倍投倍数（2x-5x，玩法类似 Powerball 的 Power Play，但不滚动头奖的那部分
不受倍投影响，跟 Power Play 的规则一致所以沿用同样的"数字紧跟标签"解析
方式）。头奖奖金结构跟 Powerball 主玩法一样，有 Estimated Jackpot +
Cash Value 两个字段。
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
log = logging.getLogger("lotto_america_scraper")

URL = "https://www.powerball.com/lotto-america"
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
class LottoAmericaResult:
    draw_date: Optional[str]
    main_balls: list[int]
    star_ball: Optional[int]
    all_star_bonus: Optional[int]
    next_drawing: dict  # {next_draw_date, estimated_jackpot, cash_value}
    winners_by_tier: dict
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


def parse_winning_numbers(text: str) -> tuple[Optional[str], list[int], Optional[int], Optional[int]]:
    pattern = re.compile(
        r"Winning Numbers\s*\n\s*"
        rf"({DATE_PATTERN})\s*\n\s*"
        r"(\d{1,2})\s*\n\s*"
        r"(\d{1,2})\s*\n\s*"
        r"(\d{1,2})\s*\n\s*"
        r"(\d{1,2})\s*\n\s*"
        r"(\d{1,2})\s*\n\s*"
        r"(\d{1,2})"
        r"(?:\s*\n\s*All Star Bonus\s*\n\s*(\d+)x)?",
        re.IGNORECASE,
    )
    m = pattern.search(text)
    if not m:
        return None, [], None, None
    draw_date = m.group(1)
    main_balls = [int(m.group(i)) for i in range(2, 7)]
    star_ball = int(m.group(7))
    all_star_bonus = int(m.group(8)) if m.group(8) else None
    return draw_date, main_balls, star_ball, all_star_bonus


def parse_next_drawing(text: str) -> dict:
    result: dict = {}
    date_m = re.search(rf"Next Drawing\s*\n\s*({DATE_PATTERN})", text, re.IGNORECASE)
    result["next_draw_date"] = date_m.group(1) if date_m else None

    jackpot_m = re.search(rf"Estimated Jackpot\s*\n\s*{MONEY_PATTERN}", text, re.IGNORECASE)
    result["estimated_jackpot"] = jackpot_m.group(1).strip() if jackpot_m else None

    cash_m = re.search(rf"Cash Value\s*\n\s*{MONEY_PATTERN}", text, re.IGNORECASE)
    result["cash_value"] = cash_m.group(1).strip() if cash_m else None

    return result


def parse_winners_section(text: str) -> dict:
    pattern = re.compile(
        rf"Winners\s*\n\s*{DATE_PATTERN}\s*\n"
        r"Lotto America\s*\n"
        rf"Jackpot Winners\s*\n{STATES_PATTERN}\s*\n"
        rf"Match 5 \+ All Star Bonus\s*\nMultiplier Winners\s*\n{STATES_PATTERN}\s*\n"
        rf"Match 5\s*\n(?:\$[\d,]+\s*)?Winners\s*\n{STATES_PATTERN}",
        re.IGNORECASE,
    )
    m = pattern.search(text)
    if not m:
        return {}

    def states_list(raw: str) -> list[str]:
        raw = raw.strip()
        return [] if raw == "None" else [s.strip() for s in raw.split(",")]

    return {
        "Jackpot": {"states": states_list(m.group(1))},
        "Match 5 + All Star Bonus": {"states": states_list(m.group(2))},
        "Match 5": {"states": states_list(m.group(3))},
    }


def validate(main_balls: list[int], star_ball: Optional[int]) -> None:
    if len(main_balls) != 5:
        raise ValueError(f"主号码数量不对,抓到 {len(main_balls)} 个,预期 5 个: {main_balls}")
    if len(set(main_balls)) != 5:
        raise ValueError(f"主号码有重复,号码不合法: {main_balls}")
    if not all(1 <= n <= 52 for n in main_balls):
        raise ValueError(f"主号码超出 1-52 范围: {main_balls}")
    if star_ball is None:
        raise ValueError("没抓到 Star Ball 号码")
    if not (1 <= star_ball <= 10):
        raise ValueError(f"Star Ball 超出 1-10 范围: {star_ball}")


def fetch() -> LottoAmericaResult:
    import requests

    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(URL, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_SEC)
            resp.raise_for_status()
            text = normalize_text(resp.text)

            draw_date, main_balls, star_ball, all_star_bonus = parse_winning_numbers(text)
            validate(main_balls, star_ball)

            return LottoAmericaResult(
                draw_date=draw_date,
                main_balls=main_balls,
                star_ball=star_ball,
                all_star_bonus=all_star_bonus,
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


def save_result(result: LottoAmericaResult) -> Path:
    out_dir = OUTPUT_DIR / "multistate" / "lotto-america"
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
