"""
Arizona Lottery — PICK 4 抓取

跟 az_pick3_scraper.py 完全一样的结构，只是4位数字、gameNum 换成 25
（"PICK 4 ONE PLAY FOR $1"）。同样按 (draw_date, session) 去重。
"""

import dataclasses
import datetime as dt
import json
import logging
import time
from pathlib import Path
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("az_pick4_scraper")

GAME_NUM = 25
API_URL = f"https://api.arizonalottery.com/v2/drawgames/{GAME_NUM}/drawings"
REFERER = "https://www.arizonalottery.com/"
USER_AGENT = "LottoLiveApp/0.1 (+mailto:YOUR-CONTACT-EMAIL@example.com)"
REQUEST_TIMEOUT_SEC = 15
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 5

SESSION_BY_SEQ = {1: "midday", 2: "evening"}

OUTPUT_DIR = Path("data")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


@dataclasses.dataclass
class Pick4Result:
    game: str
    draw_date: str
    session: str  # "midday" | "evening"
    digits: list[int]
    next_draw_date: Optional[str]
    next_session: Optional[str]
    fetched_at: str
    source_url: str

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def parse_entry(entry: dict) -> Pick4Result:
    digits = [int(n) for n in entry["winningNumbers"].split("-")]
    return Pick4Result(
        game="Pick 4",
        draw_date=entry["drawDate"],
        session=SESSION_BY_SEQ.get(entry.get("gameSeqNum"), f"seq{entry.get('gameSeqNum')}"),
        digits=digits,
        next_draw_date=entry.get("nextDrawDate"),
        next_session=SESSION_BY_SEQ.get(entry.get("nextGameSeqNum")),
        fetched_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        source_url=API_URL,
    )


def validate(result: Pick4Result) -> None:
    if len(result.digits) != 4:
        raise ValueError(f"位数不对，抓到 {len(result.digits)} 位，预期 4 位: {result.digits}")
    if not all(0 <= d <= 9 for d in result.digits):
        raise ValueError(f"数字超出 0-9 范围: {result.digits}")


def fetch() -> Pick4Result:
    import requests

    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(
                API_URL,
                headers={"User-Agent": USER_AGENT, "Referer": REFERER, "Accept": "application/json"},
                timeout=REQUEST_TIMEOUT_SEC,
            )
            resp.raise_for_status()
            entries = resp.json()
            if not entries:
                raise ValueError("接口返回了空列表，没有任何开奖记录")

            result = parse_entry(entries[0])
            validate(result)
            return result
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("第 %d 次尝试失败: %s", attempt, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SEC * attempt)
    raise RuntimeError(f"重试 {MAX_RETRIES} 次后仍然失败") from last_err


MAX_HISTORY = 100


def save_result(result: Pick4Result) -> Path:
    out_dir = OUTPUT_DIR / "arizona" / "pick-4"
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
    if history and history[0].get("draw_date") == new_entry.get("draw_date") and history[0].get("session") == new_entry.get("session"):
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
