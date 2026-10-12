"""Indiana Cash POP 抓取 — hoosierlottery.com。合规说明见 in_shared.py。

页面 /games/draw/cash-pop/ 一次性展示当天全部5个场次(Morning 9:30AM / Matinee 1:30PM /
Afternoon 3:30PM / Evening 7:30PM / Late Night 11:30PM，ET)，每个场次是一个
<div class="draw-time">，还没开奖的场次用一张占位图片(class里带"pending-drawing")，
已经开完的场次就是纯文字的号码(1-15的某个数字)，不是图片，不需要跑浏览器/OCR——
这点在写这个脚本之前专门用Wayback Machine的历史快照验证过(2026-07-25的一份快照里
同时看到了"pending"的图片态和已经开完的纯文字态)。

每次抓取把当天"所有已经开完的场次"一次性全部存下来(不是只存一个)，比按cron触发时间
点去猜"这次该是哪个场次"更稳——即使某次cron延迟触发，已经开完的场次号码还在页面上，
不会丢。

玩法本身跟"选中几个号对应一个奖级"完全不是一回事：你选1~15个号中的任意几个(每个
号单独按$1/$2/$5/$10起注)，开奖只开1个号，中了算你选的那组里是否包含这个开出来的
号——这个号本身的奖金是由一个独立的随机倍数决定的(官网"Prizes and Odds"表格列出的
5x/7x/10x/15x/20x/25x/50x/100x/250x九档及对应概率，$1/$2/$5各注额完全成比例缩放，
确认倍数本身与你选了几个号、选的是哪个号都无关)，不是"选的号数量越少奖金越高"
这种可以从官方开奖号码直接反推出来的东西——所以这个脚本只负责把"今天开出来的那个
号"抓对，倍数/奖金这块不在这里处理(app侧按binary win/lose + 展示官方倍数参考表处理，
见NumberMatchingService.matchMembershipPick)。
"""

import dataclasses
import json
from pathlib import Path
from typing import Optional

import in_shared as shared

URL = "https://www.hoosierlottery.com/games/draw/cash-pop/"
OUTPUT_DIR = Path("data")
SESSION_ORDER = ["morning", "matinee", "afternoon", "evening", "latenight"]


@dataclasses.dataclass
class CashPopResult:
    game: str
    draw_date: str
    session: str
    # One-element list, not a scalar int — matches every other game's `mainNumbersField`
    # convention (a plain array field) so the app side doesn't need a new "wrap main number"
    # capability alongside the existing special-numbers-only `wrapScalar`.
    numbers: list[int]
    fetched_at: str
    source_url: str

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def fetch() -> list[CashPopResult]:
    from bs4 import BeautifulSoup

    html = shared.fetch_html(URL)
    soup = BeautifulSoup(html, "html.parser")
    date_el = soup.select_one("section.drawing-numbers span.sub-title")
    if not date_el:
        raise ValueError("没找到日期元素")
    draw_date = shared.iso_date(date_el.get_text(strip=True))

    # The page renders the same 5 draw-time blocks three times for different responsive
    # breakpoints (desktop/tablet/mobile markup all present at once, toggled by CSS) — only the
    # first set lacks the extra "align-items-1000-start" class, so that's the one we select.
    draw_time_divs = soup.select("div.draw-time:not(.align-items-1000-start)")
    if len(draw_time_divs) != len(SESSION_ORDER):
        raise ValueError(f"场次数量不对，抓到 {len(draw_time_divs)} 个，预期 {len(SESSION_ORDER)} 个")

    results = []
    for session, div in zip(SESSION_ORDER, draw_time_divs):
        numbers_el = div.select_one(".draw-time__numbers")
        if numbers_el is None:
            raise ValueError(f"[{session}] 没找到号码元素")
        classes = numbers_el.get("class") or []
        if "pending-drawing" in classes:
            continue  # 这个场次今天还没开奖
        text = numbers_el.get_text(strip=True)
        if not text.isdigit():
            raise ValueError(f"[{session}] 号码不是纯数字: {text!r}")
        results.append(CashPopResult(
            game="Cash POP",
            draw_date=draw_date,
            session=session,
            numbers=[int(text)],
            fetched_at=shared.fetched_at_now(),
            source_url=URL,
        ))
    return results


def validate(result: CashPopResult) -> None:
    if len(result.numbers) != 1:
        raise ValueError(f"[{result.session}] 号码数不对: {result.numbers}")
    if not (1 <= result.numbers[0] <= 15):
        raise ValueError(f"[{result.session}] 号码超出 1-15 范围: {result.numbers}")


def save_results(results: list[CashPopResult]) -> Path:
    out_dir = OUTPUT_DIR / "indiana" / "cashpop"
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

    merged = new_entries + history
    merged = merged[: shared.MAX_HISTORY]

    out_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2))
    shared.log.info("已保存: %s (%d 条记录，本次更新 %d 条)", out_path, len(merged), len(new_entries))
    return out_path


def main():
    results = fetch()
    if not results:
        shared.log.warning("今天目前还没有任何场次开完奖，本次不写入")
        return
    for r in results:
        validate(r)
    shared.log.info("抓取结果: %s", [r.to_dict() for r in results])
    save_results(results)


if __name__ == "__main__":
    main()
