"""
South Carolina own-games — sourced directly from sceducationlottery.com (the real official site,
confirmed directly accessible: no Cloudflare/WAF challenge, no robots.txt restriction, plain
server-rendered HTML with no JS framework). Verified real data as of 2026-10-16.

Confirmed mechanics (quoted from sceducationlottery.com's own game pages):
  - Pick 3 / Pick 4: standard digit games with a Fireball add-on digit. "the midday draw is at
    12:59 p.m. and the evening draw is at 6:59 p.m." (per Cash POP's page, which states its own
    schedule matches Pick 3/4's "just like Pick 3 and Pick 4").
  - Cash POP: "Drawings are held every evening at 6:59 and ... Midday drawings ... Monday through
    Saturday afternoons at 12:59." Pick 1-15, single number drawn — reuses the existing
    membershipPick mechanic already built for Indiana/Maine/Maryland/Mississippi/Missouri/North
    Carolina's Cash POP.
  - Palmetto Cash 5: "Drawings are held seven days a week at 6:59 p.m." 5 of 1-42, $2/play. Lower
    tiers (Match 2/3/4) are fixed prizes confirmed from the results page itself ($1/$5/$300).
    A random per-play multiplier ("Palmetto Cash 5 Multiplier") is NOT modeled (can't be derived
    from public draw data, same class as other states' unmodeled secondary multipliers).

Page structure (all results pages share the same general accordion/table-heading layout):
  - Pick 3/Pick 4: `.drawResultsaccordion-title` blocks, each with a `.lightblue-bg span` (date),
    `.numbers-box li.number:not(.fireball)` (main digits), `.numbers-box li.number.fireball`
    (Fireball digit), and `.lightblue-bg-right div` (session: "Midday"/"Evening").
  - Cash POP: `.table-heading` blocks, each with `.lightblue-bg div` (date),
    `.CashPOPDrawResultsFont_White` (the single drawn number), `.lightblue-bg-right div` (session).
  - Palmetto Cash 5: `.table-heading` blocks, each with `.lightblue-bg` (date, no session — one
    daily draw) and `.darkblue-bg.numbers` (hyphen-separated 5 numbers, e.g. "3 - 4 - 6 - 19 - 24").
"""
import datetime as dt
import json
import logging
import re
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("sc_scraper")

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
OUTPUT_DIR = Path("data") / "south-carolina"
MAX_HISTORY = 100


def fetch_html(url: str) -> str:
    resp = requests.get(url, headers={"User-Agent": UA}, timeout=30)
    resp.raise_for_status()
    return resp.text


def parse_date(text: str) -> str:
    return dt.datetime.strptime(text.strip(), "%B %d, %Y").strftime("%Y-%m-%d")


def save_results(subdir: str, new_entries: list[dict]) -> None:
    if not new_entries:
        log.warning("no entries parsed for %s, skipping save", subdir)
        return
    out_dir = OUTPUT_DIR / subdir
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "latest.json"
    existing: list[dict] = []
    if out_path.exists():
        try:
            loaded = json.loads(out_path.read_text())
            if isinstance(loaded, list):
                existing = loaded
        except (json.JSONDecodeError, OSError):
            existing = []
    by_key = {(e.get("draw_date"), e.get("session")): e for e in existing}
    for e in new_entries:
        by_key[(e.get("draw_date"), e.get("session"))] = e
    merged = sorted(by_key.values(), key=lambda e: (e.get("draw_date", ""), e.get("session", "")), reverse=True)[:MAX_HISTORY]
    out_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2))
    log.info("已保存: %s (%d 条记录)", out_path, len(merged))


def _next_draw_date(after_date_iso: str, skip_sunday: bool) -> str:
    """sceducationlottery.com's result pages show no forward-looking 'next draw' element at all
    (confirmed live 2026-11-10) — Pick 3/4's own page text states the fixed schedule instead
    ("Evening drawings held every evening"; "Midday drawings...Monday through Saturday...No midday
    drawings are held on Sundays or on Christmas Day"), so this computes the next occurrence from
    that known schedule rather than scraping a date that isn't published."""
    d = dt.date.fromisoformat(after_date_iso)
    for offset in range(1, 8):
        candidate = d + dt.timedelta(days=offset)
        if skip_sunday and candidate.weekday() == 6:
            continue
        if candidate.month == 12 and candidate.day == 25:
            continue
        return candidate.isoformat()
    raise AssertionError("unreachable: a valid draw day occurs at least once within a week")


def scrape_digit_game(url: str) -> dict[str, list[dict]]:
    soup = BeautifulSoup(fetch_html(url), "html.parser")
    by_session: dict[str, list[dict]] = {"Midday": [], "Evening": []}
    for block in soup.select(".drawResultsaccordion-title"):
        date_span = block.select_one(".lightblue-bg span")
        box = block.select_one(".numbers-box")
        session_div = block.select_one(".lightblue-bg-right div")
        if not (date_span and box and session_div):
            continue
        session = session_div.get_text(strip=True)
        if session not in by_session:
            continue
        digits = [li.get_text(strip=True) for li in box.select("li.number") if "fireball" not in li.get("class", [])]
        fb_li = box.select_one("li.number.fireball")
        entry = {
            "draw_date": parse_date(date_span.get_text(strip=True)),
            "session": session,
            "digits": digits,
            "fireball": fb_li.get_text(strip=True) if fb_li else None,
            "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "source_url": url,
        }
        by_session[session].append(entry)
    for session, entries in by_session.items():
        if not entries:
            continue
        latest_date = max(e["draw_date"] for e in entries)
        next_date = _next_draw_date(latest_date, skip_sunday=(session == "Midday"))
        for e in entries:
            e["next_draw_date"] = next_date
    return by_session


def scrape_cashpop() -> dict[str, list[dict]]:
    url = "https://www.sceducationlottery.com/Games/CashPOP"
    soup = BeautifulSoup(fetch_html(url), "html.parser")
    by_session: dict[str, list[dict]] = {"Midday": [], "Evening": []}
    for block in soup.select(".table-heading"):
        date_div = block.select_one(".lightblue-bg div")
        num_div = block.select_one(".CashPOPDrawResultsFont_White")
        session_div = block.select_one(".lightblue-bg-right div")
        if not (date_div and num_div and session_div):
            continue
        session = session_div.get_text(strip=True)
        if session not in by_session:
            continue
        entry = {
            "draw_date": parse_date(date_div.get_text(strip=True)),
            "session": session,
            "number": num_div.get_text(strip=True),
            "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "source_url": url,
        }
        by_session[session].append(entry)
    # Cash POP's own page states the schedule matches Pick 3/4's ("just like Pick 3 and Pick 4" —
    # see module doc): Evening every day, Midday Monday-Saturday only — same `_next_draw_date`
    # helper, same skip_sunday split, as the digit games above.
    for session, entries in by_session.items():
        if not entries:
            continue
        latest_date = max(e["draw_date"] for e in entries)
        next_date = _next_draw_date(latest_date, skip_sunday=(session == "Midday"))
        for e in entries:
            e["next_draw_date"] = next_date
    return by_session


def scrape_palmettocash5() -> list[dict]:
    url = "https://www.sceducationlottery.com/Games/PalmettoCash5"
    soup = BeautifulSoup(fetch_html(url), "html.parser")
    # Confirmed live 2026-11-11: this same page shows a real forward jackpot estimate above the
    # results table ("Est. Jackpot Now" / `div.jackpot strong`, e.g. "$160,000") — real published
    # data, not a guess. No next-draw-date element on the page, but the page's own text confirms
    # "Drawings are held seven days a week at 6:59 p.m." (no skipped day), so next_draw_date is
    # computed the same way as Cash POP's Evening session above (skip_sunday=False).
    jackpot_el = soup.select_one("div.jackpot strong")
    next_jackpot_amount = jackpot_el.get_text(strip=True) if jackpot_el else None
    out = []
    for block in soup.select(".table-heading"):
        date_div = block.select_one(".lightblue-bg")
        num_div = block.select_one(".darkblue-bg.numbers")
        if not (date_div and num_div):
            continue
        nums = [n.strip() for n in num_div.get_text(strip=True).split("-") if n.strip()]
        if len(nums) != 5:
            continue
        out.append({
            "draw_date": parse_date(date_div.get_text(strip=True)),
            "numbers": nums,
            "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "source_url": url,
        })
    if out:
        latest_date = max(e["draw_date"] for e in out)
        next_date = _next_draw_date(latest_date, skip_sunday=False)
        for e in out:
            e["next_draw_date"] = next_date
            if next_jackpot_amount is not None:
                e["next_jackpot_amount"] = next_jackpot_amount
    return out


def main() -> None:
    pick3 = scrape_digit_game("https://www.sceducationlottery.com/Games/Pick3")
    save_results("pick3-midday", pick3["Midday"])
    save_results("pick3-evening", pick3["Evening"])

    pick4 = scrape_digit_game("https://www.sceducationlottery.com/Games/Pick4")
    save_results("pick4-midday", pick4["Midday"])
    save_results("pick4-evening", pick4["Evening"])

    cashpop = scrape_cashpop()
    save_results("cashpop-midday", cashpop["Midday"])
    save_results("cashpop-evening", cashpop["Evening"])

    pc5 = scrape_palmettocash5()
    save_results("palmettocash5", pc5)

    log.info("完成")


if __name__ == "__main__":
    main()
