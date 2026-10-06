"""
Arizona Lottery — PICK 3 抓取

同样直接打 api.arizonalottery.com 的 JSON API。跟 THE PICK/FANTASY 5/
TRIPLE TWIST 不一样的地方：
- 号码是3个"数字"（每位0-9，允许重复），不是从一个号码池里选不重复的号。
- 一天开两次（Midday + Evening），gameSeqNum=1 是 Midday，=2 是 Evening
  （从接口数据里"下一期日期"的进位方式反推出来的：同一天内 seq1 的
  nextGameSeqNum 指向当天的 seq2，seq2 的则指向次日的 seq1）。
- 没有头奖/彩池概念——官网原话："Unlike jackpot games ... daily games pay
  out an established fixed prize to every single winning ticket."，所以
  这里不抓 jackpotAmount 这类字段（接口本来也不会给，这是固定奖）。
- gameNum 用 6（"PICK 3 ONE PLAY FOR $1"）。接口里还有个 gameNum=76
  （"PICK 3 TWO PLAYS FOR $1"），是同一组开奖号码的另一种玩法/赔付表，
  开奖数据完全一样，不需要重复抓。

因为一天两期，latest.json 用 (draw_date, session) 一起去重，不能只按
draw_date——不然同一天的 Midday 会被 Evening 覆盖掉。
"""

import dataclasses
import datetime as dt
import json
import logging
import time
from pathlib import Path
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("az_pick3_scraper")

GAME_NUM = 6
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
class Pick3Result:
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


def parse_entry(entry: dict) -> Pick3Result:
    digits = [int(n) for n in entry["winningNumbers"].split("-")]
    return Pick3Result(
        game="Pick 3",
        draw_date=entry["drawDate"],
        session=SESSION_BY_SEQ.get(entry.get("gameSeqNum"), f"seq{entry.get('gameSeqNum')}"),
        digits=digits,
        next_draw_date=entry.get("nextDrawDate"),
        next_session=SESSION_BY_SEQ.get(entry.get("nextGameSeqNum")),
        fetched_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        source_url=API_URL,
    )


def validate(result: Pick3Result) -> None:
    if len(result.digits) != 3:
        raise ValueError(f"位数不对，抓到 {len(result.digits)} 位，预期 3 位: {result.digits}")
    if not all(0 <= d <= 9 for d in result.digits):
        raise ValueError(f"数字超出 0-9 范围: {result.digits}")


def fetch() -> Pick3Result:
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


def save_result(result: Pick3Result) -> Path:
    """按 (draw_date, session) 去重 —— 一天两期，只按日期去重会把同一天
    的 Midday 结果被 Evening 结果覆盖掉。
    """
    out_dir = OUTPUT_DIR / "arizona" / "pick-3"
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
