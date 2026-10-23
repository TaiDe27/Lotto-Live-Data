"""
Maine own-games — mainelottery.com homepage (server-rendered, real data embedded directly —
confirmed 2026-10-14 this identifying UA gets a clean 200 with real numbers, unlike an earlier
session's wrong assumption that this site needed JS rendering/lotteryusa.com).

The homepage lists every game's single latest draw in one fixed, repeating sequence of
`.win-numbers` blocks (confirmed order: 0=Powerball Xs&Os, 1=Powerball, 2=Powerball Double Play,
3=Tri-State Megabucks Plus, 4=Mega Millions, 5=Millionaire for Life, 6=Lotto America, 7=Gimme 5,
8=Pick 3 Day, 9=Pick 3 Eve, 10=Pick 4 Day, 11=Pick 4 Eve, 12=Cash POP(5 sessions)) — only the LATEST
draw per game, no history, so this only ever writes a 1-entry-per-run update merged into history
exactly like every other scraper in this repo.

Tri-State Megabucks Plus (index 3, shared with New Hampshire/Vermont) is intentionally NOT captured
here — it needs its own state-agnostic game id once NH/VT are onboarded, not a Maine-only one;
capturing it now under a "maine" path would need re-keying later. Flagged, not built this round.
"""
import dataclasses
import datetime as dt
import json
import logging
import re
import time
from pathlib import Path
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("me_scraper")

URL = "https://www.mainelottery.com/"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
REQUEST_TIMEOUT_SEC = 15
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 5
OUTPUT_DIR = Path("data") / "maine"


def iso_date(raw: str) -> str:
    """e.g. 'Tuesday 10/13/2026' -> '2026-10-13'."""
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", raw)
    if not m:
        raise ValueError(f"无法解析日期: {raw!r}")
    mm, dd, yyyy = m.groups()
    return f"{yyyy}-{int(mm):02d}-{int(dd):02d}"


@dataclasses.dataclass
class Result:
    game: str
    draw_date: str
    numbers: list[int]
    fetched_at: str
    source_url: str

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def _date_for_block(block) -> str:
    prev = block.find_previous(string=re.compile(r"\d{1,2}/\d{1,2}/\d{4}"))
    if prev is None:
        raise ValueError("block 前面找不到日期文本")
    return iso_date(str(prev))


def fetch_all() -> tuple[dict[str, list[Result]], list[tuple[str, Exception]]]:
    import requests
    from bs4 import BeautifulSoup

    last_err = None
    html = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(URL, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_SEC)
            resp.raise_for_status()
            html = resp.text
            break
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("第 %d 次尝试失败: %s", attempt, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SEC * attempt)
    if html is None:
        raise RuntimeError("重试多次后仍然失败") from last_err

    soup = BeautifulSoup(html, "html.parser")
    blocks = soup.select(".win-numbers")
    if len(blocks) < 13:
        raise ValueError(f"预期至少 13 个 .win-numbers block，实际只找到 {len(blocks)} 个 — 页面结构可能变了")

    out: dict[str, list[Result]] = {}
    errors: list[tuple[str, Exception]] = []

    # Per-section try/except (added 2026-10-23, defensive — no real incident observed for ME
    # specifically yet, unlike PA/WI/MD's confirmed ones, but the homepage's block-per-game layout
    # has the same theoretical exposure: an unguarded int() on a not-yet-drawn session's text would
    # crash fetch_all() entirely, losing every OTHER already-parsed game's result too (worse blast
    # radius than PA/WI since even gimme5 — not even a multi-session game — would be lost). Isolating
    # each section the same way guards against that regardless of whether it's ever actually fired.
    def _digits_or_none(text: str) -> Optional[int]:
        return int(text) if text.isdigit() else None

    def simple_pool(idx: int, subdir: str, label: str, expected_count: int):
        try:
            block = blocks[idx]
            nums = [n for s in block.select("span") if (n := _digits_or_none(s.get_text(strip=True))) is not None]
            # drop the trailing special-ball number if this game has one and it's included in the span scan
            nums = nums[:expected_count]
            if len(nums) != expected_count:
                log.warning("[%s] 号码数 %d，预期 %d: %s", label, len(nums), expected_count, nums)
                return
            draw_date = _date_for_block(block)
            out[subdir] = [Result(game=label, draw_date=draw_date, numbers=nums,
                                   fetched_at=dt.datetime.now(dt.timezone.utc).isoformat(), source_url=URL)]
        except Exception as e:  # noqa: BLE001
            log.error("[%s] 失败: %s", label, e)
            errors.append((label, e))

    simple_pool(7, "gimme5", "Gimme 5", 5)

    def digit_block(idx: int, subdir: str, label: str, session: str, count: int):
        try:
            block = blocks[idx]
            digits = [n for s in block.select(".ball span") if (n := _digits_or_none(s.get_text(strip=True))) is not None]
            if len(digits) != count:
                log.warning("[%s] 位数 %d，预期 %d: %s", label, len(digits), count, digits)
                return
            draw_date = _date_for_block(block)
            # "digits"/"session" keys match this project's shared ScrapedDigitGameResult mapper
            # (draw_date/session/digits) — same shape every other state's Pick 3/4 scraper emits.
            out.setdefault(subdir, []).append({
                "game": label, "draw_date": draw_date, "session": session, "digits": digits,
                "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(), "source_url": URL,
            })
        except Exception as e:  # noqa: BLE001
            log.error("[%s] 失败: %s", label, e)
            errors.append((label, e))

    digit_block(8, "pick3", "Pick 3 (Day)", "day", 3)
    digit_block(9, "pick3", "Pick 3 (Evening)", "evening", 3)
    digit_block(10, "pick4", "Pick 4 (Day)", "day", 4)
    digit_block(11, "pick4", "Pick 4 (Evening)", "evening", 4)

    # Cash POP: one block, 5 cycles each with its own single ball — emit 5 separate "session" results.
    cp_block = blocks[12]
    cycle_labels = {"cycle1": "early_bird", "cycle2": "brunch", "cycle3": "matinee",
                     "cycle4": "supper_time", "cycle5": "night_owl"}
    cp_draw_date = _date_for_block(cp_block)
    cp_results = []
    for div in cp_block.find_all("div", recursive=True):
        cls = div.get("class") or []
        for c in cls:
            if c in cycle_labels:
                session = cycle_labels[c]
                ball_div = div.find_next_sibling("div", class_="ball")
                if ball_div:
                    num = _digits_or_none(ball_div.get_text(strip=True))
                    if num is not None:
                        cp_results.append((session, num))
    out["cashpop"] = [
        Result(game=f"Cash POP ({session})", draw_date=cp_draw_date, numbers=[num],
               fetched_at=dt.datetime.now(dt.timezone.utc).isoformat(), source_url=URL)
        for session, num in cp_results
    ]
    # Cash POP's latest.json needs a `session` key the Swift side's shared membership-pick mapper
    # reads — add it as a dict key via a thin wrapper dataclass instead of overloading Result.
    out["cashpop_sessions"] = [{"session": s, "draw_date": cp_draw_date, "numbers": [n]} for s, n in cp_results]
    # Return whatever sections succeeded regardless of `errors` - raising here (even an aggregate
    # error at the very end) would discard `out` entirely before main() ever gets to save it,
    # defeating the whole point of isolating sections above. main() decides whether to still
    # surface `errors` for visibility, AFTER saving what's already safely available.
    return out, errors


def save(subdir: str, entries) -> None:
    out_dir = OUTPUT_DIR / subdir
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
    new_entries = [e.to_dict() if hasattr(e, "to_dict") else e for e in entries]
    if new_entries and "session" in new_entries[0]:
        # Pick 3/4 (day+evening) and Cash POP (5 cycles) share one draw_date per run — dedupe by
        # (draw_date, session) so one session's entry doesn't silently clobber another's.
        new_keys = {(e["draw_date"], e["session"]) for e in new_entries}
        history = [h for h in history if (h.get("draw_date"), h.get("session")) not in new_keys]
    else:
        new_keys = {e["draw_date"] for e in new_entries}
        history = [h for h in history if h.get("draw_date") not in new_keys]
    merged = (new_entries + history)[:100]
    out_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2))
    log.info("已保存: %s (%d 条)", out_path, len(merged))


def main():
    all_results, errors = fetch_all()
    for subdir, entries in all_results.items():
        if subdir == "cashpop":
            continue  # raw per-session tuples only, superseded by cashpop_sessions
        # cashpop_sessions is the real output but the config/cachePath this app reads is keyed
        # "cashpop" (matching every other game's subdir-equals-config-name convention) — rename
        # on save rather than carry the "_sessions" suffix into the live data path.
        out_subdir = "cashpop" if subdir == "cashpop_sessions" else subdir
        save(out_subdir, entries)
    if errors:
        raise RuntimeError(f"{len(errors)} 个游戏抓取失败: {errors}")
    log.info("完成")


if __name__ == "__main__":
    main()
