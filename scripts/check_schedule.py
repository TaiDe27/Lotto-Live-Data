"""
轻量版"现在该不该抓取"检查 —— 给每个彩种的高频轮询 job 用。

跟 compute_schedule.py 的区别:这个文件完全不 import zoneinfo,不做任何
时区换算,只读 compute_schedule.py 每天生成的 data/schedule/today.json,
拿里面现成的 UTC 时间戳跟当前 UTC 时间做字符串/数值比较。所有需要"知道
现在是不是该抓取"的彩种,不管有多少个,都只依赖这一份每天生成一次的文件,
不需要各自重复算时区、重复 import zoneinfo。

用法(GitHub Actions 里):
    python3 scripts/check_schedule.py
    退出码 0 = 该抓取,1 = 还没到时间或今天不开奖,2 = 调度文件缺失/过期
"""

from __future__ import annotations
import datetime as dt
import json
import sys
from pathlib import Path

UTC = dt.timezone.utc
SCHEDULE_FILE = Path(__file__).parent.parent / "data" / "schedule" / "today.json"

# 调度文件如果是"昨天"生成的还没被今天的 daily job 更新,说明 daily job
# 可能挂了或者还没跑,这种情况不能信任这份文件里的时间戳,要显式报错而不是
# 悄悄拿旧数据比对(旧数据比对出来的结果毫无意义,还可能误判成"该抓取")
MAX_SCHEDULE_FILE_AGE_HOURS = 26  # 比 24 小时稍微宽松一点,容忍 daily job 触发时间的正常抖动


def check() -> tuple[int, dict]:
    if not SCHEDULE_FILE.exists():
        return 2, {"error": f"调度文件不存在: {SCHEDULE_FILE},daily job 是不是还没跑过?"}

    schedule = json.loads(SCHEDULE_FILE.read_text())
    now_utc = dt.datetime.now(UTC)

    computed_at = dt.datetime.fromisoformat(schedule["computed_at_utc"])
    age_hours = (now_utc - computed_at).total_seconds() / 3600
    if age_hours > MAX_SCHEDULE_FILE_AGE_HOURS:
        return 2, {
            "error": f"调度文件太旧了({age_hours:.1f} 小时前生成的),"
                     f"daily job 可能没有正常运行,不能信任这份数据",
            "computed_at_utc": schedule["computed_at_utc"],
        }

    if not schedule["is_draw_day"]:
        return 1, {"reason": "今天(美东日期)不是开奖日", "date_et": schedule["date_et"]}

    window_start = dt.datetime.fromisoformat(schedule["window_start_utc"])
    window_end = dt.datetime.fromisoformat(schedule["window_end_utc"])

    in_window = window_start <= now_utc <= window_end
    debug = {
        "now_utc": now_utc.isoformat(),
        "window_start_utc": schedule["window_start_utc"],
        "window_end_utc": schedule["window_end_utc"],
        "in_window": in_window,
    }
    return (0 if in_window else 1), debug


if __name__ == "__main__":
    code, debug = check()
    for k, v in debug.items():
        print(f"{k}: {v}")
    sys.exit(code)
