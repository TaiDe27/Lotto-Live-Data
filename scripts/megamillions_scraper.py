"""
Mega Millions 官方 JSON API 抓取 —— megamillions.com/cmspages/utilservice.asmx/GetLatestDrawData

跟 powerball_scraper.py 完全不同的套路:不是从渲染后的 HTML 页面里用正则抠
文字,而是直接调用 megamillions.com 自己前端在用的同一个 ASP.NET AJAX 接口
(用户在浏览器 DevTools 的 Network 面板里抓到的),拿到的是结构化 JSON——
不用猜页面结构,也不用担心"改版后正则就失效"这类风险(除非他们换接口)。

响应是经典 ASP.NET PageMethod 的双重 JSON:外层 {"d": "<JSON字符串>"},
"d" 的值本身又是一段 JSON 文本,要 json.loads 两次才能拿到真正的数据。

⚠️ 已知风险:实测抓到的请求头里带了 Cloudflare 的 __cfwaitingroom cookie,
说明这个站点挂了 Cloudflare 的流量保护。纯 requests 脚本(没有真实浏览器
指纹、没有预先的 cookie 会话)在 GitHub Actions 的出口 IP 上会不会被拦,
没有实测验证过——如果上线后发现抓到的不是 JSON 而是验证页/错误页,
resp.json() 这一步会直接抛异常、重试 3 次后失败,不会把错误数据悄悄存进
latest.json,但需要人工去看一下日志、换个应对思路(比如带上更完整的请求头
组合,或者研究一下这个 __cfwaitingroom 是不是真的会拦截非浏览器请求)。

⚠️ 范围说明:这一版只抓了卡片要用的核心字段(号码、本期/下期奖池、
Match 5 中奖情况)。接口里还有 PrizeTiers(每个奖级按倍数细分的中奖人数)
和 PrizeMatrix(固定赔率表,含 Megaplier 2x-10x 对应的奖金)没有抓——
如果以后要做 Mega Millions 自己的 Payout 详情页(类似 Powerball 那个),
这两块数据就在同一个响应里,到时候再加字段去取就行,不需要重新找接口。
"""

import dataclasses
import datetime as dt
import json
import logging
import time
from pathlib import Path
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("megamillions_scraper")

URL = "https://www.megamillions.com/cmspages/utilservice.asmx/GetLatestDrawData"
SOURCE_URL = "https://www.megamillions.com/"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT_SEC = 15
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 5
MAX_HISTORY = 100

OUTPUT_DIR = Path("data")


@dataclasses.dataclass
class MegaMillionsResult:
    draw_date: Optional[str]
    white_balls: list[int]
    mega_ball: Optional[int]
    # None when the API reports -1 ("no single multiplier" — Megaplier has been a random
    # per-ticket bonus baked into the price since 2025, not one drawn/announced value).
    megaplier: Optional[int]
    current_prize_pool: Optional[float]
    current_cash_value: Optional[float]
    jackpot_winners: Optional[int]
    next_draw_date: Optional[str]
    next_prize_pool: Optional[float]
    next_cash_value: Optional[float]
    match5_winner_text: Optional[str]
    match5_locations: list
    fetched_at: str
    source_url: str

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def validate(white_balls: list[int], mega_ball: Optional[int]) -> None:
    if len(white_balls) != 5:
        raise ValueError(f"白球数量不对,抓到 {len(white_balls)} 个,预期 5 个: {white_balls}")
    if len(set(white_balls)) != 5:
        raise ValueError(f"白球有重复,号码不合法: {white_balls}")
    if not all(1 <= n <= 70 for n in white_balls):
        raise ValueError(f"白球超出 1-70 范围: {white_balls}")
    if mega_ball is None:
        raise ValueError("没抓到 Mega Ball 号码")
    if not (1 <= mega_ball <= 24):
        raise ValueError(f"Mega Ball 超出 1-24 范围: {mega_ball}")


def fetch() -> MegaMillionsResult:
    import requests

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Content-Type": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": "https://www.megamillions.com",
        "Referer": SOURCE_URL,
    }

    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.post(URL, headers=headers, data=b"", timeout=REQUEST_TIMEOUT_SEC)
            resp.raise_for_status()
            # 外层 {"d": "..."},"d" 的值是另一段要再解一次的 JSON 字符串。
            outer = resp.json()
            payload = json.loads(outer["d"])

            drawing = payload["Drawing"]
            jackpot = payload.get("Jackpot") or {}
            match_winners = payload.get("MatchWinners") or {}
            match_locations = payload.get("MatchWinnersLocation") or []

            white_balls = [drawing["N1"], drawing["N2"], drawing["N3"], drawing["N4"], drawing["N5"]]
            mega_ball = drawing.get("MBall")
            validate(white_balls, mega_ball)

            megaplier_raw = drawing.get("Megaplier")
            megaplier = megaplier_raw if megaplier_raw and megaplier_raw > 0 else None

            return MegaMillionsResult(
                draw_date=drawing.get("PlayDate"),
                white_balls=white_balls,
                mega_ball=mega_ball,
                megaplier=megaplier,
                current_prize_pool=jackpot.get("CurrentPrizePool"),
                current_cash_value=jackpot.get("CurrentCashValue"),
                jackpot_winners=jackpot.get("Winners"),
                next_draw_date=payload.get("NextDrawingDate"),
                next_prize_pool=jackpot.get("NextPrizePool"),
                next_cash_value=jackpot.get("NextCashValue"),
                match5_winner_text=match_winners.get("WinnerText"),
                match5_locations=match_locations,
                fetched_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                source_url=SOURCE_URL,
            )
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("第 %d 次尝试失败: %s", attempt, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SEC * attempt)
    raise RuntimeError(f"重试 {MAX_RETRIES} 次后仍然失败") from last_err


def save_result(result: MegaMillionsResult) -> Path:
    """同 powerball_scraper.save_result:只维护一份 latest.json 数组,最新在
    最前,按 draw_date 去重,最多 MAX_HISTORY 条。"""
    out_dir = OUTPUT_DIR / "multistate" / "mega-millions"
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
