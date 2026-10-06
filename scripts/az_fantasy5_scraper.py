"""
Arizona Lottery — FANTASY 5 抓取

同 az_the_pick_scraper.py 的接口/做法（直接打 api.arizonalottery.com 的
JSON API，不抓 HTML），只是 gameNum/号码规则不同。FANTASY 5 的 gameNum 是
4，选5个号（1-41），无特别球。
"""

import dataclasses
import datetime as dt
import json
import logging
import time
from pathlib import Path
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("az_fantasy5_scraper")

GAME_NUM = 4
API_URL = f"https://api.arizonalottery.com/v2/drawgames/{GAME_NUM}/drawings"
REFERER = "https://www.arizonalottery.com/"
USER_AGENT = "LottoLiveApp/0.1 (+mailto:YOUR-CONTACT-EMAIL@example.com)"
REQUEST_TIMEOUT_SEC = 15
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 5

OUTPUT_DIR = Path("data")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


@dataclasses.dataclass
class Fantasy5Result:
    game: str
    draw_date: str
    numbers: list[int]
    jackpot_amount: Optional[int]
    next_draw_date: Optional[str]
    next_jackpot_amount: Optional[int]
    winners_by_division: dict
    fetched_at: str
    source_url: str

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def parse_entry(entry: dict) -> Fantasy5Result:
    numbers = [int(n) for n in entry["winningNumbers"].split("-")]
    divisions = {k: v for k, v in (entry.get("divisionCounts") or {}).items() if v}
    return Fantasy5Result(
        game="Fantasy 5",
        draw_date=entry["drawDate"],
        numbers=numbers,
        jackpot_amount=entry.get("jackpotAmount"),
        next_draw_date=entry.get("nextDrawDate"),
        next_jackpot_amount=entry.get("nextJackpotAmount"),
        winners_by_division=divisions,
        fetched_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        source_url=API_URL,
    )


def validate(result: Fantasy5Result) -> None:
    if len(result.numbers) != 5:
        raise ValueError(f"号码数量不对，抓到 {len(result.numbers)} 个，预期 5 个: {result.numbers}")
    if len(set(result.numbers)) != 5:
        raise ValueError(f"号码有重复，不合法: {result.numbers}")
    if not all(1 <= n <= 41 for n in result.numbers):
        raise ValueError(f"号码超出 1-41 范围: {result.numbers}")


def fetch() -> Fantasy5Result:
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


def save_result(result: Fantasy5Result) -> Path:
    out_dir = OUTPUT_DIR / "arizona" / "fantasy-5"
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
