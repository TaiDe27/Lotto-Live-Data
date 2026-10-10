"""Georgia Fantasy 5 抓取 — 共享逻辑见 ga_shared.py。

一天一期(没有session)。号池范围(1-42)取自公开已知规则，官网页面本身是JS
渐进渲染，没在静态HTML/JS里找到明文"1到42"这句话——如果实际开奖号码出现
>42，这个假设就错了，需要人工复核(validate() 里按 1-42 校验，超出范围会直接
报错而不是静默接受，方便尽早发现)。
"""

import json
from pathlib import Path
from typing import Optional

import ga_shared as shared

ESA_GAME_NAME = "FANTASY 5"
OUT_SUBDIR = "fantasy5"
MAIN_COUNT = 5
ASSUMED_MAX_NUMBER = 42  # 未在页面文本里确认，来自公开规则，需要人工复核
BACKFILL_COUNT = 7

OUTPUT_DIR = Path("data")


def to_record(draw: dict) -> dict:
    numbers = [int(x) for x in draw["results"][0]["primary"]]
    if len(numbers) != MAIN_COUNT:
        raise ValueError(f"号码数不对，抓到 {len(numbers)} 个: {numbers}")
    if not all(1 <= n <= ASSUMED_MAX_NUMBER for n in numbers):
        raise ValueError(f"号码超出假设的 1-{ASSUMED_MAX_NUMBER} 范围，需要人工复核: {numbers}")

    jackpot_amount: Optional[int] = None
    jackpots = draw.get("jackpots")
    if jackpots:
        jackpot_amount = jackpots[0].get("amount")

    return {
        "game": "Fantasy 5",
        "draw_date": shared.draw_date_iso(draw),
        "numbers": numbers,
        "jackpot_amount": jackpot_amount,
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

    new_keys = {e["draw_date"] for e in records}
    history = [h for h in history if h.get("draw_date") not in new_keys]

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
