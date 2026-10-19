"""Virginia Lottery own-games scraper.

Source: valottery.com's own `/api/v1/downloadall?gameId=<id>` endpoint - a plain-text,
semicolon-delimited full-history export, found via the user's own browser devtools (the
`gameId` query params themselves came from `data-game-id`/`downloadall?gameId=` attributes
embedded in each game's own `/data/draw-games/<slug>` page, cross-referenced this session) and
re-verified with a plain `requests.get` (HTTP 200, no auth needed). Unlike most other states'
sources, this single endpoint returns the FULL published history per game (thousands of lines
back to ~2015), not just the latest draw - this scraper only keeps the newest `MAX_HISTORY`.

Game IDs (confirmed this session from each game's own page):
  1030 Cash 5 with EZ Match   1070 Bank a Million   1050 Pick 3 with FIREBALL
  1040 Pick 4 with FIREBALL   1035 Pick 5 with FIREBALL   40 Cash Pop

Confirmed real mechanics (from each game's own `/data/draw-games/<slug>` how-to-play text):
  - Cash 5: 5 of 1-45, $1/play, nightly 11pm ET. EZ Match is a separate instant-win add-on not
    computable from the public draw feed (same class as other states' EZ Match/Cash Pop-style
    add-ons) - not modeled.
  - Bank a Million: 6 of 1-40 plus a separately-drawn Bonus Ball (not player-chosen). Real wager
    can be split across prize tiers ($2 single play / two $1 plays / four 50-cent plays, each with
    a different capped top prize) - this app models only the standard $2 single-play prize tier;
    the wager-split variants are out of scope (same "not modeled" class as other states' split-
    wager options).
  - Pick 3/4/5 with FIREBALL: Day AND Night drawings every day, each digit game modeled as two
    separate sessions of the existing Fireball-addon mechanic (same `positionalDigitsWithAddOn`
    kind already used for IN/NJ/IL/CT/MO's Pick 3/4). Pick 5 is a genuinely 5-digit game (00000-
    99999) - VA is this project's first Pick 5.
  - Cash Pop: 5 real sessions/day (Coffee Break 9am, Lunch Break 12pm, Rush Hour 5pm, Prime Time
    9pm, After Hours 11:59pm ET, all confirmed from the game's own page) - same few-session-per-day
    mechanic as IN/ME/MD/MS/MO/NC's Cash POP (`membershipPick`), well within the project's standing
    <=10-draws/day cutoff.
"""
import datetime as dt
import json
import logging
import re
import time
from pathlib import Path

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("va_scraper")

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
HEADERS = {"User-Agent": UA}
DOWNLOAD_URL = "https://www.valottery.com/api/v1/downloadall"
OUTPUT_DIR = Path("data") / "virginia"
MAX_HISTORY = 100


def fetch_lines(game_id: int) -> list[str]:
    r = requests.get(DOWNLOAD_URL, headers=HEADERS, params={"gameId": game_id}, timeout=20)
    r.raise_for_status()
    lines = r.text.splitlines()
    return [ln.strip() for ln in lines if ln.strip() and ";" in ln]


def mdy_to_iso(mdy: str) -> str:
    m, d, y = mdy.split("/")
    return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"


def save(subdir: str, entries: list[dict]):
    out_dir = OUTPUT_DIR / subdir
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "latest.json"
    out_path.write_text(json.dumps(entries[:MAX_HISTORY], ensure_ascii=False, indent=2))
    log.info("saved %s (%d entries)", out_path, min(len(entries), MAX_HISTORY))


def scrape_numbers(game_id: int, subdir: str, name: str):
    out = []
    for line in fetch_lines(game_id):
        m = re.match(r"(\d{1,2}/\d{1,2}/\d{4});\s*([\d,]+)$", line)
        if not m:
            continue
        out.append({
            "draw_date": mdy_to_iso(m.group(1)),
            "numbers": [int(x) for x in m.group(2).split(",")],
            "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "source_url": f"https://www.valottery.com/data/draw-games/cash5?gameId={game_id}",
        })
    save(subdir, out)
    log.info("%s: %d entries, latest %s", name, len(out), out[0] if out else None)


def scrape_bank_a_million():
    out = []
    for line in fetch_lines(1070):
        m = re.match(r"(\d{1,2}/\d{1,2}/\d{4});\s*([\d,]+);\s*Bonus Ball:\s*(\d+)$", line)
        if not m:
            continue
        out.append({
            "draw_date": mdy_to_iso(m.group(1)),
            "numbers": [int(x) for x in m.group(2).split(",")],
            "bonus_ball": int(m.group(3)),
            "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "source_url": "https://www.valottery.com/data/draw-games/bankamillion?gameId=1070",
        })
    save("bank-a-million", out)
    log.info("bank-a-million: %d entries, latest %s", len(out), out[0] if out else None)


def scrape_pick_with_fireball(game_id: int, subdir: str, name: str):
    pattern = re.compile(
        r"(\d{1,2}/\d{1,2}/\d{4});\s*Day:\s*([\d,]+);\s*Fireball:\s*(\d+);\s*"
        r"Night:\s*([\d,]+);\s*Fireball:\s*(\d+)$"
    )
    out = []
    for line in fetch_lines(game_id):
        m = pattern.match(line)
        if not m:
            continue
        draw_date = mdy_to_iso(m.group(1))
        fetched_at = dt.datetime.now(dt.timezone.utc).isoformat()
        source_url = f"https://www.valottery.com/data/draw-games/{subdir}?gameId={game_id}"
        out.append({
            "game": name, "draw_date": draw_date, "session": "day",
            "digits": [int(x) for x in m.group(2).split(",")],
            "fireball": int(m.group(3)),
            "fetched_at": fetched_at, "source_url": source_url,
        })
        out.append({
            "game": name, "draw_date": draw_date, "session": "night",
            "digits": [int(x) for x in m.group(4).split(",")],
            "fireball": int(m.group(5)),
            "fetched_at": fetched_at, "source_url": source_url,
        })
    save(subdir, out)
    log.info("%s: %d entries, latest %s", name, len(out), out[0] if out else None)


def scrape_cash_pop():
    pattern = re.compile(
        r"[A-Za-z]{3}\s+(\d{1,2}/\d{1,2}/\d{4})\s*-\s*\d{1,2}:\d{2}\s*[AP]M;\s*(\d+);\s*(.+)$"
    )
    out = []
    for line in fetch_lines(40):
        m = pattern.match(line)
        if not m:
            continue
        out.append({
            "draw_date": mdy_to_iso(m.group(1)),
            "session": m.group(3).strip().lower().replace(" ", "-"),
            "number": int(m.group(2)),
            "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "source_url": "https://www.valottery.com/data/draw-games/cashpop?gameId=40",
        })
    save("cash-pop", out)
    log.info("cash-pop: %d entries, latest %s", len(out), out[0] if out else None)


def main():
    errors = []
    jobs = [
        ("Cash 5", lambda: scrape_numbers(1030, "cash5", "Cash 5")),
        ("Bank a Million", scrape_bank_a_million),
        ("Pick 3", lambda: scrape_pick_with_fireball(1050, "pick3", "Pick 3")),
        ("Pick 4", lambda: scrape_pick_with_fireball(1040, "pick4", "Pick 4")),
        ("Pick 5", lambda: scrape_pick_with_fireball(1035, "pick5", "Pick 5")),
        ("Cash Pop", scrape_cash_pop),
    ]
    for name, fn in jobs:
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            log.error("%s failed: %s", name, e)
            errors.append((name, e))
        time.sleep(1)

    if errors:
        raise RuntimeError(f"{len(errors)} game(s) failed: {errors}")


if __name__ == "__main__":
    main()
