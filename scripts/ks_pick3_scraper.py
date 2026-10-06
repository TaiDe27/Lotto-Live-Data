"""
Kansas Lottery — Pick 3 抓取

直接打 playonkansas.com 自己前端用的后台 JSON API
(gateway-web.loyalty.playonkansas.com)——完全公开，不需要任何 Referer/
认证头，比亚利桑那那边还简单，确认过可以直接 curl。

gameId 映射（从 /services/jackpot/api/v1/jackpot-results/games 这个"所有
游戏汇总"接口里核对出来的，id=9 的 name 字段就叫 "Pick3 Midday"，id=10 叫
"Pick3 Evening"）：
  - gameId=9  -> Pick 3 Midday（下午）
  - gameId=10 -> Pick 3 Evening（晚上）

⚠️ 日期陷阱：接口返回的 drawDate 是 UTC 时间戳（比如晚场
"2026-10-06T02:10:00Z"，就是堪萨斯当地（Central Time）10/5 晚上9:10），
接口自己带的 drawingDate 字段只是直接取 UTC 的日期部分，没有换算时区，会
比当地实际开奖日期晚一天——所以这份脚本不信任 drawingDate，自己把 drawDate
转成 America/Chicago 再取日期，跟官网页面上显示的日期才能对上。

Pick 3 没有头奖/彩池概念（固定奖），接口本身对这两个 gameId 也不提供"下一期
开奖时间"（games 汇总接口里这两条的 nextDrawDate 是 null），所以不抓这个
字段——反正是固定每天两场，App 那边可以自己推算下一场。
"""

import dataclasses
import datetime as dt
import json
import logging
import time
import zoneinfo
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ks_pick3_scraper")

API_BASE = "https://gateway-web.loyalty.playonkansas.com/services/jackpot/api/v1/jackpot-results"
GAME_IDS = {"midday": 9, "evening": 10}
USER_AGENT = "LottoLiveApp/0.1 (+mailto:YOUR-CONTACT-EMAIL@example.com)"
REQUEST_TIMEOUT_SEC = 15
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 5
FETCH_COUNT = 6  # 一天两期，6条≈最近3天，兜住单次漏跑（同 az_pick3_scraper.py 的做法）

CENTRAL_TZ = zoneinfo.ZoneInfo("America/Chicago")

OUTPUT_DIR = Path("data")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


@dataclasses.dataclass
class Pick3Result:
    game: str
    draw_date: str
    session: str  # "midday" | "evening"
    digits: list[int]
    fetched_at: str
    source_url: str

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def central_date(draw_date_utc: str) -> str:
    """"2026-10-06T02:10:00Z" -> "2026-10-05"（换算到 America/Chicago 再取日期，
    不要直接截取 UTC 日期部分，晚场会差一天，见文件顶部注释）。"""
    instant = dt.datetime.fromisoformat(draw_date_utc.replace("Z", "+00:00"))
    return instant.astimezone(CENTRAL_TZ).date().isoformat()


def parse_entry(entry: dict, session: str, source_url: str) -> Pick3Result:
    digits = [d["data"] for d in sorted(entry["resultData"], key=lambda d: d["order"])]
    return Pick3Result(
        game="Pick 3",
        draw_date=central_date(entry["drawDate"]),
        session=session,
        digits=digits,
        fetched_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        source_url=source_url,
    )


def validate(result: Pick3Result) -> None:
    if len(result.digits) != 3:
        raise ValueError(f"位数不对，抓到 {len(result.digits)} 位，预期 3 位: {result.digits}")
    if not all(0 <= d <= 9 for d in result.digits):
        raise ValueError(f"数字超出 0-9 范围: {result.digits}")


def fetch_session(session: str) -> list[Pick3Result]:
    import requests

    game_id = GAME_IDS[session]
    url = (
        f"{API_BASE}?gameId={game_id}&jackpotStatus=COMPLETE&page=0"
        f"&size={FETCH_COUNT}&sort=externalId,drawDate,desc&doublePlay=FALSE"
    )

    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}, timeout=REQUEST_TIMEOUT_SEC)
            resp.raise_for_status()
            content = resp.json().get("content", [])
            if not content:
                raise ValueError(f"接口返回了空列表（{session}），没有任何开奖记录")

            results = [parse_entry(e, session, url) for e in content]
            for r in results:
                validate(r)
            return results
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("[%s] 第 %d 次尝试失败: %s", session, attempt, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SEC * attempt)
    raise RuntimeError(f"[{session}] 重试 {MAX_RETRIES} 次后仍然失败") from last_err


def fetch() -> list[Pick3Result]:
    return fetch_session("midday") + fetch_session("evening")


MAX_HISTORY = 100


def save_results(results: list[Pick3Result]) -> Path:
    """按 (draw_date, session) 去重合并——一天两期，只按日期去重会把同一天
    的 Midday 结果被 Evening 结果覆盖掉。本次抓到的几期直接覆盖旧 history
    里对应的记录，其余没碰到的旧记录保留不动。
    """
    out_dir = OUTPUT_DIR / "kansas" / "pick-3"
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

    # 合并后按 (draw_date, session) 倒序排好，而不是简单拼接——两个场次各自
    # 抓回来的 FETCH_COUNT 条是分开有序的，直接拼接会让整体顺序乱掉。
    # session 按"当天实际先后顺序"映射成数字再排序，不能直接比较字符串——
    # "evening"在字母序上小于"midday"，直接字符串倒序会把同一天的 midday
    # 排到 evening 前面，顺序刚好反了。
    session_rank = {"midday": 0, "evening": 1}
    merged = new_entries + history
    merged.sort(key=lambda e: (e["draw_date"], session_rank.get(e.get("session"), -1)), reverse=True)
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
