"""
每天跑一次的调度计算任务 —— 唯一一个需要导入 zoneinfo、做时区换算的地方。

产出:data/schedule/today.json,内容是"今天(美东日期)是不是开奖日、如果是
目标抓取窗口的 UTC 时间戳是多少"。其余所有抓取任务的 workflow(不管有多少
个彩种)都只读这个文件、拿现成的 UTC 时间戳做字符串/数值比较,不需要各自
重复 import zoneinfo、重复算一遍时区偏移——时区计算这件"每天只会变化一次"
的事情,只应该做一次,不应该在几十个高频轮询的 job 里各算各的。

建议这个脚本用一个独立的、每天只触发一次的 workflow 来跑(比如 UTC 00:05,
保证赶在当天可能出现的最早开奖窗口之前),产出的文件 commit 回仓库,后续
所有抓取 workflow 都依赖这次 commit 之后的版本。
"""

from __future__ import annotations
import datetime as dt
import json
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
UTC = dt.timezone.utc

DRAW_WEEKDAYS = {0, 2, 5}  # 周一/三/六(Python: Monday=0)
DRAW_TIME_ET = dt.time(22, 59)
TARGET_DELAY_MINUTES = 30
WINDOW_TOLERANCE_MINUTES = 5

SCHEDULE_FILE = Path(__file__).parent.parent / "data" / "schedule" / "today.json"


def compute_today_schedule(now_utc: dt.datetime | None = None) -> dict:
    """以"美东日期"为准判断今天是不是开奖日,算出目标抓取窗口的 UTC 时间戳。

    不是开奖日就返回 is_draw_day: false,其余字段为 null——下游的轻量检查
    脚本看到 is_draw_day 是 false 就直接跳过,不需要再看任何时间戳。
    """
    now_utc = now_utc or dt.datetime.now(UTC)
    now_et = now_utc.astimezone(ET)
    today_et = now_et.date()

    result = {
        "date_et": today_et.isoformat(),
        "is_draw_day": today_et.weekday() in DRAW_WEEKDAYS,
        "draw_time_utc": None,
        "target_scrape_utc": None,
        "window_start_utc": None,
        "window_end_utc": None,
        "et_utc_offset_hours": now_et.utcoffset().total_seconds() / 3600,
        "computed_at_utc": now_utc.isoformat(),
    }

    if result["is_draw_day"]:
        draw_dt_et = dt.datetime.combine(today_et, DRAW_TIME_ET, tzinfo=ET)
        target_et = draw_dt_et + dt.timedelta(minutes=TARGET_DELAY_MINUTES)
        window_start_et = target_et - dt.timedelta(minutes=WINDOW_TOLERANCE_MINUTES)
        window_end_et = target_et + dt.timedelta(minutes=WINDOW_TOLERANCE_MINUTES)

        result["draw_time_utc"] = draw_dt_et.astimezone(UTC).isoformat()
        result["target_scrape_utc"] = target_et.astimezone(UTC).isoformat()
        result["window_start_utc"] = window_start_et.astimezone(UTC).isoformat()
        result["window_end_utc"] = window_end_et.astimezone(UTC).isoformat()

    return result


def main():
    schedule = compute_today_schedule()
    SCHEDULE_FILE.parent.mkdir(parents=True, exist_ok=True)
    SCHEDULE_FILE.write_text(json.dumps(schedule, ensure_ascii=False, indent=2))
    print(f"已写入 {SCHEDULE_FILE}:")
    print(json.dumps(schedule, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
