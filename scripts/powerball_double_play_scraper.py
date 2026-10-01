"""
Powerball Double Play 抓取 — powerball.com/double-play

跟主 Powerball 抓取(powerball_scraper.py)共用同一套"标签词 + 正则"解析
逻辑,因为用户截图确认了这个页面的 Winning Numbers 区块结构跟主页几乎
一样(日期 + 6个数字,后面是 View Results / Check Your Numbers 按钮)。

⚠️ 可信度分级(重要,别把这份代码当成跟主脚本一样可靠):
- 号码抓取部分(parse_winning_numbers):结构跟已验证过的主页面一致,
  可信度较高
- 奖金抓取部分(parse_next_drawing 的 "Top Prize" 分支):只根据截图
  推测,还没拿真实文本流跑过验证,上线前必须实际跑一次这份脚本、把
  结果贴出来核对
- Winners 区块(parse_winners_section):**这份脚本没有单独适配**——
  主脚本里那个函数是按主 Powerball 页面"Match 5 + Power Play"这类
  tier 名字写的正则,Double Play 页面的 tier 名字是"Double Play"
  "Match 5"这种不同的措辞,直接套用大概率抓不到东西(不会报错,只是
  抓到空 dict)。这部分先留空,等有真实文本样本了再补。
"""

import dataclasses
import datetime as dt
import json
import logging
import time
from pathlib import Path
from typing import Optional

import sys
sys.path.insert(0, str(Path(__file__).parent))
from powerball_scraper import (  # noqa: E402  复用主脚本已验证过的函数
    normalize_text,
    parse_winning_numbers,
    parse_next_drawing,
    validate,
    USER_AGENT,
    REQUEST_TIMEOUT_SEC,
    MAX_RETRIES,
    RETRY_BACKOFF_SEC,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("powerball_double_play_scraper")

URL = "https://www.powerball.com/double-play"
OUTPUT_DIR = Path("data")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


@dataclasses.dataclass
class DoublePlayResult:
    game: str
    draw_date: Optional[str]
    white_balls: list[int]
    red_ball: Optional[int]
    next_drawing: dict
    fetched_at: str
    source_url: str

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def fetch() -> DoublePlayResult:
    import requests

    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(URL, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_SEC)
            resp.raise_for_status()
            text = normalize_text(resp.text)

            draw_date, white_balls, red_ball, _power_play = parse_winning_numbers(text)
            # Double Play 没有 Power Play,_power_play 理论上应该是 None;
            # 如果不是 None,说明页面结构比预期复杂,建议报出来看一眼
            if _power_play is not None:
                log.warning(
                    "Double Play 页面竟然抓到了 Power Play 倍数(%s),"
                    "跟预期结构不符,建议核对页面是否变了", _power_play
                )
            validate(white_balls, red_ball)

            next_drawing = parse_next_drawing(text)

            return DoublePlayResult(
                game="Powerball Double Play",
                draw_date=draw_date,
                white_balls=white_balls,
                red_ball=red_ball,
                next_drawing=next_drawing,
                fetched_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                source_url=URL,
            )
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("第 %d 次尝试失败: %s", attempt, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SEC * attempt)
    raise RuntimeError(f"重试 {MAX_RETRIES} 次后仍然失败") from last_err


MAX_HISTORY = 100


def save_result(result: DoublePlayResult) -> Path:
    """同 powerball_scraper.save_result:只维护一份 latest.json 数组,最新
    在最前,按 draw_date 去重,最多 MAX_HISTORY 条,不再按日期单独存文件。"""
    out_dir = OUTPUT_DIR / "multistate" / "powerball-double-play"
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
