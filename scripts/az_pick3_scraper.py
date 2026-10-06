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

每次运行会处理最近 FETCH_COUNT 期（不是只取最新一条）：这个游戏一天开两
次，如果某次运行只抓"最新一条"，而触发时机恰好只覆盖了其中一个场次（比如
只在晚上触发），当天的 Midday 那期就会永远漏进 latest.json。抓最近几期、
按 (draw_date, session) 去重合并，不管一天触发几次、谁先谁后，最终都能把
当天两期都收全，也顺带兜住了"漏跑一整天"这种情况（FETCH_COUNT=6，覆盖
最近3天）。
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
FETCH_COUNT = 6  # 一天两期，6条≈最近3天，足够兜住单次漏跑

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


def parse_entry(entry: dict, fetched_at: str) -> Pick3Result:
    digits = [int(n) for n in entry["winningNumbers"].split("-")]
    return Pick3Result(
        game="Pick 3",
        draw_date=entry["drawDate"],
        session=SESSION_BY_SEQ.get(entry.get("gameSeqNum"), f"seq{entry.get('gameSeqNum')}"),
        digits=digits,
        next_draw_date=entry.get("nextDrawDate"),
        next_session=SESSION_BY_SEQ.get(entry.get("nextGameSeqNum")),
        fetched_at=fetched_at,
        source_url=API_URL,
    )


def validate(result: Pick3Result) -> None:
    if len(result.digits) != 3:
        raise ValueError(f"位数不对，抓到 {len(result.digits)} 位，预期 3 位: {result.digits}")
    if not all(0 <= d <= 9 for d in result.digits):
        raise ValueError(f"数字超出 0-9 范围: {result.digits}")


def fetch() -> list[Pick3Result]:
    """返回最近 FETCH_COUNT 期，新的在前 —— 不只是最新一条。"""
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

            fetched_at = dt.datetime.now(dt.timezone.utc).isoformat()
            results = [parse_entry(e, fetched_at) for e in entries[:FETCH_COUNT]]
            for result in results:
                validate(result)
            return results
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("第 %d 次尝试失败: %s", attempt, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SEC * attempt)
    raise RuntimeError(f"重试 {MAX_RETRIES} 次后仍然失败") from last_err


MAX_HISTORY = 100


def save_results(results: list[Pick3Result]) -> Path:
    """按 (draw_date, session) 去重合并 —— 一天两期，只按日期去重会把同
    一天的 Midday 结果被 Evening 结果覆盖掉。本次抓到的几期直接覆盖旧
    history 里对应的记录（更新更可信），其余没碰到的旧记录保留不动。
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

    new_entries = [r.to_dict() for r in results]
    new_keys = {(e["draw_date"], e["session"]) for e in new_entries}
    history = [h for h in history if (h.get("draw_date"), h.get("session")) not in new_keys]

    merged = new_entries + history
    merged = merged[:MAX_HISTORY]

    out_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2))
    log.info("已保存: %s (%d 条记录，本次更新 %d 条)", out_path, len(merged), len(new_entries))
    return out_path


def main():
    results = fetch()
    log.info("抓取结果: %s", [r.to_dict() for r in results])
    save_results(results)


if __name__ == "__main__":
    main()
