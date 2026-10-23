"""
Maryland own-games — mdlottery.com itself (NOT lotteryusa.com).

Corrects a wrong conclusion from the first build pass: the official site IS plain
server-rendered HTML for every game below (confirmed 2026-10-14 with this project's own
identifying scraper UA, no browser/session needed) — the earlier "results feed loads via a
separate call this session didn't have time to reverse-engineer" note was simply an incomplete
investigation, not a real technical block. Pick 3/4/5 share one page
(mdlottery.com/games/pick-3-pick-4-pick-5/) with three `.single-winning-numbers.pick-N` blocks;
Cash POP, Bonus Match 5, and Multi-Match each have their own page, structurally identical
(`.result-container` inside a `.single-winning-numbers.<slug>` wrapper, with the draw date in
`.result-col.date p`).

Cash POP draws 4x/day (9am/1pm/6pm/11pm ET, confirmed from the page's own How-to-Play text) —
same `membershipPick` mechanic already built for Indiana/Maine/Kentucky-investigated-and-rejected.
Unlike Indiana's fixed $1/POP, Maryland lets you wager $1/$2/$5/$10 per POP — doesn't change the
win/loss determination (still binary: did the drawn number fall in your chosen set), so no scraper
change needed for that, it only affects a player's real payout multiplier which this app already
can't compute from public draw data for any Cash POP variant.

Output field shapes (draw_date/session/digits for digit games, draw_date/numbers/bonus_number for
pool games, draw_date/session/numbers for Cash POP) are kept IDENTICAL to the original
lotteryusa.com-sourced scraper so the Swift side's `liveMapping`/hardcoded-fallback field names
don't need to change for Pick 3/4/5/Bonus Match 5/Multi-Match — only Cash POP is new.
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
log = logging.getLogger("md_scraper")

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
# mdlottery.com's WAF 403s a bare User-Agent-only request (confirmed 2026-10-15) — needs a fuller,
# browser-like header set (incl. Sec-Fetch-*/Upgrade-Insecure-Requests) to pass reliably; a
# User-Agent+Accept-Language-only set still passed sometimes but 403'd intermittently in repeated
# testing, this fuller set passed every time it was tried. Accept-Encoding deliberately excludes
# "br" (Brotli): without the optional `brotli` package installed, `requests` can't decode a Brotli
# response body and silently hands back garbled bytes instead of erroring, which is worse than just
# not offering it.
REQUEST_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}
REQUEST_TIMEOUT_SEC = 15
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 5
OUTPUT_DIR = Path("data") / "maryland"

# Cash POP's 4 real sessions (9am/1pm/6pm/11pm ET) keyed to the same session-name convention
# this project already uses for Indiana ("morning"/"matinee"/"afternoon"/"evening"/"latenight")
# and Maine ("early_bird"/"brunch"/...) — Maryland only has 4, not 5, so its own distinct map.
SESSION_BY_HOUR_LABEL = {
    "9 A.M.": "morning",
    "1 P.M.": "afternoon",
    "6 P.M.": "evening",
    "11 P.M.": "latenight",
}


def iso_date(raw: str) -> str:
    # mdlottery.com prints dates as "10/13/26" (M/D/YY) or "10/13/2026" (Cash POP's "AS OF" line)
    raw = raw.strip()
    for fmt in ("%m/%d/%y", "%m/%d/%Y"):
        try:
            return dt.datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    raise ValueError(f"unrecognized date format: {raw!r}")


def _fetch_soup(url: str):
    import requests
    from bs4 import BeautifulSoup
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT_SEC)
            resp.raise_for_status()
            return BeautifulSoup(resp.text, "html.parser")
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("[%s] 第 %d 次尝试失败: %s", url, attempt, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SEC * attempt)
    raise RuntimeError(f"[{url}] 重试多次后仍然失败") from last_err


def _balls_to_digits(ul) -> list[int]:
    # mdlottery.com renders the Evening row's markup *before* that draw actually happens, with
    # each ball showing a literal "-" placeholder instead of a digit — confirmed via a real
    # incident (2026-10-22/23) where this crashed on int("-") before the caller's own
    # `len(digits) != n` check ever got a chance to treat "not drawn yet" as benign (which it
    # already does - this was the only thing actually broken). Skip non-numeric ball text instead
    # of raising, so the caller just sees a short list, same as a missing row entirely.
    out = []
    for li in ul.select("li"):
        classes = li.get("class") or []
        if "multiplier" in classes and "label" in classes:
            continue  # the "Midday"/"Evening" text label li, not a real ball
        text = li.get_text(strip=True)
        if not text.isdigit():
            continue
        out.append(int(text))
    return out


def fetch_pick345() -> dict[str, list[dict]]:
    """Pick 3/4/5 all live on one page, one `.single-winning-numbers.pick-N` block each."""
    soup = _fetch_soup("https://www.mdlottery.com/games/pick-3-pick-4-pick-5/")
    now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
    results: dict[str, list[dict]] = {"pick3": [], "pick4": [], "pick5": []}
    for n, key in ((3, "pick3"), (4, "pick4"), (5, "pick5")):
        block = soup.select_one(f".single-winning-numbers.pick-{n}")
        if not block:
            log.warning("pick-%d block not found", n)
            continue
        date_el = block.select_one(".result-col.date p")
        if not date_el:
            continue
        draw_date = iso_date(date_el.get_text(strip=True))
        for session_class, session in (("midday", "midday"), ("evening", "evening")):
            row = block.select_one(f".result-drawing-row.{session_class}")
            if not row:
                continue
            digits = _balls_to_digits(row.select_one("ul.balls"))
            if len(digits) != n:
                log.warning("pick-%d %s: expected %d digits, got %s", n, session, n, digits)
                continue
            results[key].append({
                "game": f"Pick {n} ({session.capitalize()})",
                "draw_date": draw_date,
                "session": session,
                "digits": digits,
                "fetched_at": now_iso,
                "source_url": "https://www.mdlottery.com/games/pick-3-pick-4-pick-5/",
            })
    return results


def fetch_pool(slug: str, label: str, main_count: int, has_bonus: bool) -> list[dict]:
    url = f"https://www.mdlottery.com/games/{slug}/"
    soup = _fetch_soup(url)
    block = soup.select_one(f".single-winning-numbers.{slug}")
    if not block:
        log.warning("%s: block not found", slug)
        return []
    date_el = block.select_one(".result-col.date p")
    if not date_el:
        return []
    draw_date = iso_date(date_el.get_text(strip=True))
    row = block.select_one(".result-drawing-row")
    if not row:
        return []
    balls = row.select("ul.balls > li")
    bonus_number = None
    numbers = []
    for li in balls:
        text = li.get_text(strip=True)
        if not text.isdigit():
            continue  # "-" placeholder - this draw hasn't happened yet, same as Pick 3/4/5 above
        val = int(text)
        if has_bonus and "bonus" in (li.get("class") or []):
            bonus_number = val
        else:
            numbers.append(val)
    if len(numbers) != main_count:
        log.info("%s: not drawn yet (expected %d main numbers, got %s) - normal if before draw time", slug, main_count, numbers)
        return []
    entry = {
        "game": label, "draw_date": draw_date, "numbers": numbers,
        "jackpot_amount": None,  # not shown on this page for either game; see fork report
        "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(), "source_url": url,
    }
    if has_bonus:
        entry["bonus_number"] = bonus_number
    return [entry]


def fetch_cashpop() -> list[dict]:
    url = "https://www.mdlottery.com/games/cash-pop/"
    soup = _fetch_soup(url)
    block = soup.select_one(".single-winning-numbers.cash-pop")
    if not block:
        log.warning("cash-pop: block not found")
        return []
    container = block.select_one(".result-container")
    if not container:
        return []
    ball = container.select_one("ul.balls li")
    if not ball:
        return []
    ball_text = ball.get_text(strip=True)
    if not ball_text.isdigit():
        return []  # "-" placeholder - this session hasn't drawn yet
    number = int(ball_text)
    # "AS OF 10/13/2026 11 P.M." — the <p> right after the balls row, possibly with a <br> before
    # the "DRAW #..." strong tag, so take only the first line of its text.
    p = container.find("p")
    if not p:
        return []
    first_line = p.get_text(" ", strip=True).split("DRAW")[0].strip()
    m = re.match(r"AS OF (\d{1,2}/\d{1,2}/\d{4}) (\d{1,2} [AP]\.M\.)", first_line, re.IGNORECASE)
    if not m:
        log.warning("cash-pop: couldn't parse %r", first_line)
        return []
    draw_date = iso_date(m.group(1))
    hour_label = m.group(2).upper()
    session = SESSION_BY_HOUR_LABEL.get(hour_label)
    if not session:
        log.warning("cash-pop: unrecognized session label %r", hour_label)
        return []
    return [{
        "game": "Cash POP", "draw_date": draw_date, "session": session, "numbers": [number],
        "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(), "source_url": url,
    }]


def save(subdir: str, new_entries: list[dict], key_fn) -> None:
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
    new_keys = {key_fn(e) for e in new_entries}
    history = [h for h in history if key_fn(h) not in new_keys]
    merged = (new_entries + history)[:100]
    out_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2))
    log.info("已保存: %s (%d 条，本次更新 %d 条)", out_path, len(merged), len(new_entries))


def main():
    by_date = lambda e: e["draw_date"]
    by_date_session = lambda e: (e["draw_date"], e["session"])

    # Per-game try/except isolation (same pattern as mi_scraper.py/pa_scraper.py) - previously a
    # single uncaught exception from any one of these (e.g. the int("-") crash fixed above) killed
    # main() immediately, so none of the OTHER games even got attempted that run.
    errors = []

    try:
        picks = fetch_pick345()
        save("pick3", picks["pick3"], by_date_session)
        save("pick4", picks["pick4"], by_date_session)
        save("pick5", picks["pick5"], by_date_session)
    except Exception as e:  # noqa: BLE001
        log.error("pick345 失败: %s", e)
        errors.append(("pick345", e))

    try:
        save("bonusmatch5", fetch_pool("bonus-match-5", "Bonus Match 5", 5, True), by_date)
    except Exception as e:  # noqa: BLE001
        log.error("bonusmatch5 失败: %s", e)
        errors.append(("bonusmatch5", e))

    try:
        save("multimatch", fetch_pool("multi-match", "Multi-Match", 6, False), by_date)
    except Exception as e:  # noqa: BLE001
        log.error("multimatch 失败: %s", e)
        errors.append(("multimatch", e))

    try:
        save("cashpop", fetch_cashpop(), by_date_session)
    except Exception as e:  # noqa: BLE001
        log.error("cashpop 失败: %s", e)
        errors.append(("cashpop", e))

    if errors:
        raise RuntimeError(f"{len(errors)} 个游戏抓取失败: {errors}")
    log.info("完成")


if __name__ == "__main__":
    main()
