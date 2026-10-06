"""
Kansas Lottery — Super Kansas Cash 抓取

同 ks_pick3_scraper.py 的接口/做法（gateway-web.loyalty.playonkansas.com
的公开 JSON API，不需要任何特殊请求头）。gameId=8。

每期开奖数据来自两个接口拼起来：
  1. /jackpot-results?gameId=8&...  —— 历史开奖号码 + 当期的 cashAmount
     （这期彩池/头奖金额，持续没人中就一直往上涨，不需要像 Powerball 那样
     "借用上一条记录的 next 字段"这种 lookback 技巧——这期自己的
     cashAmount 就是这期真实的头奖金额）。
  2. /jackpot-results/games —— 所有游戏的"下一期开奖时间+预计头奖"汇总，
     从里面挑出 gameId==8 的那条，拿 nextDrawDate/nextCashAmount。

同样有 drawDate 是 UTC 时间戳、drawingDate 字段没做时区换算会差一天的坑
（见 ks_pick3_scraper.py 顶部注释），一律自己用 drawDate 换算 America/
Chicago 的日期。
"""

import dataclasses
import datetime as dt
import json
import logging
import time
import zoneinfo
from pathlib import Path
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ks_super_kansas_cash_scraper")

RESULTS_URL = (
    "https://gateway-web.loyalty.playonkansas.com/services/jackpot/api/v1/jackpot-results"
    "?gameId=8&jackpotStatus=COMPLETE&page=0&size=100&sort=externalId,drawDate,desc&doublePlay=FALSE"
)
GAMES_SUMMARY_URL = "https://gateway-web.loyalty.playonkansas.com/services/jackpot/api/v1/jackpot-results/games"
GAME_ID = 8
USER_AGENT = "LottoLiveApp/0.1 (+mailto:YOUR-CONTACT-EMAIL@example.com)"
REQUEST_TIMEOUT_SEC = 15
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 5

CENTRAL_TZ = zoneinfo.ZoneInfo("America/Chicago")

OUTPUT_DIR = Path("data")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


@dataclasses.dataclass
class SuperKansasCashResult:
    game: str
    draw_date: str
    numbers: list[int]
    cash_ball: int
    jackpot_amount: Optional[float]
    next_draw_date: Optional[str]
    next_jackpot_amount: Optional[float]
    fetched_at: str
    source_url: str

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def central_date(draw_date_utc: str) -> str:
    instant = dt.datetime.fromisoformat(draw_date_utc.replace("Z", "+00:00"))
    return instant.astimezone(CENTRAL_TZ).date().isoformat()


def fetch_with_retries(url: str) -> dict:
    import requests

    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}, timeout=REQUEST_TIMEOUT_SEC)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("第 %d 次尝试失败 (%s): %s", attempt, url, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SEC * attempt)
    raise RuntimeError(f"重试 {MAX_RETRIES} 次后仍然失败: {url}") from last_err


def parse_entry(entry: dict, next_draw_date: Optional[str], next_jackpot: Optional[float]) -> SuperKansasCashResult:
    normal = sorted((d for d in entry["resultData"] if d["type"] == "NORMAL"), key=lambda d: d["order"])
    special = next((d for d in entry["resultData"] if d["type"] == "SPECIAL"), None)
    if special is None:
        raise ValueError(f"没找到 Cash Ball（SPECIAL 类型的号码）: {entry}")

    return SuperKansasCashResult(
        game="Super Kansas Cash",
        draw_date=central_date(entry["drawDate"]),
        numbers=[d["data"] for d in normal],
        cash_ball=special["data"],
        jackpot_amount=entry.get("cashAmount") or None,
        next_draw_date=next_draw_date,
        next_jackpot_amount=next_jackpot,
        fetched_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        source_url=RESULTS_URL,
    )


def validate(result: SuperKansasCashResult) -> None:
    if len(result.numbers) != 5:
        raise ValueError(f"号码数量不对，抓到 {len(result.numbers)} 个，预期 5 个: {result.numbers}")
    if len(set(result.numbers)) != 5:
        raise ValueError(f"号码有重复，不合法: {result.numbers}")
    if not all(1 <= n <= 32 for n in result.numbers):
        raise ValueError(f"号码超出 1-32 范围: {result.numbers}")
    if not (1 <= result.cash_ball <= 25):
        raise ValueError(f"Cash Ball 超出 1-25 范围: {result.cash_ball}")


def fetch() -> SuperKansasCashResult:
    results_payload = fetch_with_retries(RESULTS_URL)
    content = results_payload.get("content", [])
    if not content:
        raise ValueError("接口返回了空列表，没有任何开奖记录")

    games_payload = fetch_with_retries(GAMES_SUMMARY_URL)
    game_summary = next((g for g in games_payload if g.get("gameId") == GAME_ID), None)
    next_draw_date = central_date(game_summary["nextDrawDate"]) if game_summary and game_summary.get("nextDrawDate") else None
    next_jackpot = game_summary.get("nextCashAmount") if game_summary else None

    result = parse_entry(content[0], next_draw_date, next_jackpot)
    validate(result)
    return result


MAX_HISTORY = 100


def save_result(result: SuperKansasCashResult) -> Path:
    out_dir = OUTPUT_DIR / "kansas" / "super-kansas-cash"
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
