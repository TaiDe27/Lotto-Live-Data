"""Georgia Five 抓取 — 共享逻辑见 ga_shared.py。

⚠️ 不是"5个不重复号码从一个池子里选"的 Fantasy 5 玩法，是5位数字(0-9可重复)的
positional-digit玩法——官网真实开奖出现过 "2,5,5,6,1" 这种带重复数字的结果，
已用API数据证实。一天两期：Midday / Evening(没有 Night)。
"""

import json
from pathlib import Path

import ga_shared as shared

ESA_GAME_NAME = "GEORGIA FIVE"
OUT_SUBDIR = "georgia-five"
DIGIT_COUNT = 5
BACKFILL_COUNT = 10

OUTPUT_DIR = Path("data")


def to_record(draw: dict) -> dict:
    digits = [int(x) for x in draw["results"][0]["primary"]]
    if len(digits) != DIGIT_COUNT:
        raise ValueError(f"位数不对，抓到 {len(digits)} 位: {digits}")
    return {
        "game": "Georgia Five",
        "draw_date": shared.draw_date_iso(draw),
        "session": shared.session_name(draw),
        "digits": digits,
        "fetched_at": shared.fetched_at_now(),
        "source_url": shared.SOURCE_PAGE,
    }


def save_results(records: list[dict]) -> Path:
    out_dir = OUTPUT_DIR / "georgia" / OUT_SUBDIR
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

    def key(e):
        return (e["draw_date"], e.get("session"))

    new_keys = {key(e) for e in records}
    history = [h for h in history if key(h) not in new_keys]

    merged = records + history
    merged = merged[: shared.MAX_HISTORY]

    out_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2))
    shared.log.info("georgia/%s: 已保存 %s (%d 条，本次新增 %d 条)", OUT_SUBDIR, out_path, len(merged), len(records))
    return out_path


def main():
    draws = shared.fetch_recent_draws(ESA_GAME_NAME, BACKFILL_COUNT)
    records = [to_record(d) for d in draws]
    shared.log.info("抓取结果: %s", records)
    save_results(records)


if __name__ == "__main__":
    main()
