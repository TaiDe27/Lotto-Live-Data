"""
Arizona Lottery — THE PICK 抓取

跟 powerball_scraper.py 那批不一样：不抓 HTML 页面，直接打
api.arizonalottery.com 这个公开 JSON API（亚利桑那彩票自己的网站前端也是
靠这个接口渲染数据的，不是我们反推猜的）。这个 API 子域名本身没有挡
Cloudflare 人机验证（主站 www.arizonalottery.com 有挡，这个没有），用
requests 直接拿，不需要无头浏览器。

接口：GET /v2/drawgames/{gameNum}/drawings —— 返回这个 gameNum 最近约180
天的开奖历史（新的在前）。THE PICK 的 gameNum 是 26（另外还有个历史上的
gameNum=1，是同一个游戏的旧 SKU，这里只用 26，和官网当前渲染的一致）。

只带一个 Referer 头模拟来自官网的请求，没有用到任何 API key/认证——确认
过这个接口公开可直接访问。

返回字段里没有各奖级的具体赔付金额（只有中奖人数 divisionCounts），赔付
金额是该游戏规则本身固定/浮动的静态信息，不是每期都会变的开奖数据，这份
脚本只抓"这期实际开出了什么、中了多少人"这类事实字段。
"""

import dataclasses
import datetime as dt
import json
import logging
import time
from pathlib import Path
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("az_the_pick_scraper")

GAME_NUM = 26
API_URL = f"https://api.arizonalottery.com/v2/drawgames/{GAME_NUM}/drawings"
REFERER = "https://www.arizonalottery.com/"
USER_AGENT = "LottoLiveApp/0.1 (+mailto:YOUR-CONTACT-EMAIL@example.com)"
REQUEST_TIMEOUT_SEC = 15
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 5

OUTPUT_DIR = Path("data")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


@dataclasses.dataclass
class ThePickResult:
    game: str
    draw_date: str
    numbers: list[int]
    jackpot_amount: Optional[int]
    next_draw_date: Optional[str]
    next_jackpot_amount: Optional[int]
    winners_by_division: dict  # API 原始的 divisionN -> 中奖人数，过滤掉恒为 0 的档位
    fetched_at: str
    source_url: str

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def parse_entry(entry: dict) -> ThePickResult:
    numbers = [int(n) for n in entry["winningNumbers"].split("-")]
    divisions = {
        k: v for k, v in (entry.get("divisionCounts") or {}).items() if v
    }
    return ThePickResult(
        game="The Pick",
        draw_date=entry["drawDate"],
        numbers=numbers,
        jackpot_amount=entry.get("jackpotAmount"),
        next_draw_date=entry.get("nextDrawDate"),
        next_jackpot_amount=entry.get("nextJackpotAmount"),
        winners_by_division=divisions,
        fetched_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        source_url=API_URL,
    )


def validate(result: ThePickResult) -> None:
    if len(result.numbers) != 6:
        raise ValueError(f"号码数量不对，抓到 {len(result.numbers)} 个，预期 6 个: {result.numbers}")
    if len(set(result.numbers)) != 6:
        raise ValueError(f"号码有重复，不合法: {result.numbers}")
    if not all(1 <= n <= 40 for n in result.numbers):
        raise ValueError(f"号码超出 1-40 范围: {result.numbers}")


def fetch() -> ThePickResult:
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


def save_result(result: ThePickResult) -> Path:
    """只维护一份 latest.json，内容是最近开奖记录组成的数组（最新的在最前
    面），最多保留 MAX_HISTORY 条。按 draw_date 去重：同一期开奖只占数组里
    的一条，后一次抓到的数据覆盖前一次。
    """
    out_dir = OUTPUT_DIR / "arizona" / "the-pick"
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
