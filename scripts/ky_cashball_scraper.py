"""Kentucky Cash Ball 225 — official API (gameNumber=13), real draw ~11pm ET daily.

2026-11-13更新：同一个官方API，infoRequest 换成 "5"（不是抓历史结果用的"2"）会
返回一个完全不同形状的响应，里面直接带 NEXT_DRAW_DATE 字段（实测确认：
{"GAME_NUMBER":[13],"DRAW_DATE":[...],"GAME_NAME":["CASHBALL"],
"NEXT_DRAW_DATE":[...]}）——不是自己按"每天开"推算出来的，是官方API自己返回的
下一期日期，比猜排期更可靠。Cash Ball 225 顶奖是固定$225,000，不是滚存jackpot，
这个游戏本来就没有 nextJackpot 的需求（跟现有 jackpot:none 配置一致）。"""

import dataclasses
import datetime as dt
import json
import logging
import time
from pathlib import Path
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ky_cashball_scraper")

URL = "https://www.kylottery.com/webhandlers/WinningNumbers.xhtml"
GAME_NUMBER = "13"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
REQUEST_TIMEOUT_SEC = 15
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 5
OUTPUT_DIR = Path("data")


@dataclasses.dataclass
class CashBallResult:
    game: str
    draw_date: str
    numbers: list[int]
    bonus_number: Optional[int]
    fetched_at: str
    source_url: str
    next_draw_date: Optional[str] = None

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def ms_to_iso_date(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc).strftime("%Y-%m-%d")


def fetch_next_draw_date() -> Optional[str]:
    """同一个官方API，infoRequest="5" 返回里直接带 NEXT_DRAW_DATE（一个单元素
    列表），跟抓历史结果用的 infoRequest="2" 是两个不同形状的响应，不冲突。拿不到
    时返回 None，不让这个失败影响主开奖结果抓取。"""
    import requests

    try:
        resp = requests.post(
            URL,
            headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"},
            json={"gameNumber": GAME_NUMBER, "infoRequest": "5"},
            timeout=REQUEST_TIMEOUT_SEC,
        )
        resp.raise_for_status()
        data = resp.json()
        values = data.get("NEXT_DRAW_DATE")
        if not values:
            return None
        return ms_to_iso_date(values[0])
    except Exception as e:  # noqa: BLE001
        log.warning("拿下一期开奖日期失败（不影响本期开奖结果的抓取）: %s", e)
        return None


def fetch() -> list[CashBallResult]:
    import requests

    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.post(
                URL,
                headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"},
                json={"gameNumber": GAME_NUMBER, "infoRequest": "2"},
                timeout=REQUEST_TIMEOUT_SEC,
            )
            resp.raise_for_status()
            data = resp.json()
            values = data.get("DRAW_VALUES")
            if not values:
                raise ValueError(f"响应里没有 DRAW_VALUES: {data}")
            values = sorted(values, key=lambda v: v["DRAW_NUMBER_POSITION"])
            numbers = [v["DRAW_VALUE"] for v in values]
            if len(numbers) != 4:
                raise ValueError(f"主号码数量不是4个: {numbers}")
            bonus = (data.get("SPECIAL_ARGS") or {}).get("CASHBALL")
            result = CashBallResult(
                game="Cash Ball 225",
                draw_date=ms_to_iso_date(data["DRAW_DATE"]),
                numbers=numbers,
                bonus_number=bonus,
                fetched_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                source_url=URL,
                next_draw_date=fetch_next_draw_date(),
            )
            return [result]
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("第 %d 次尝试失败: %s", attempt, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SEC * attempt)
    raise RuntimeError("重试多次后仍然失败") from last_err


def save_results(results: list[CashBallResult]) -> Path:
    out_dir = OUTPUT_DIR / "kentucky" / "cashball"
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
    new_keys = {e["draw_date"] for e in new_entries}
    history = [h for h in history if h.get("draw_date") not in new_keys]
    merged = (new_entries + history)[:100]
    out_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2))
    log.info("已保存: %s (%d 条，本次更新 %d 条)", out_path, len(merged), len(new_entries))
    return out_path


def main():
    results = fetch()
    log.info("抓取结果: %s", [r.to_dict() for r in results])
    save_results(results)


if __name__ == "__main__":
    main()
