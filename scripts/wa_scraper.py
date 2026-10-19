"""Washington's Lottery own-games scraper.

Source: walottery.com's own classic ASP.NET `WinningNumbers/PastDrawings.aspx?gamename=<id>`
pages (`lotto`, `hit5`, `match4`, `pick3`, `cashpop`) - found via the user's own browser devtools
capture of the homepage (which showed Lotto/Hit 5's latest numbers) plus this session's own
follow-up browsing to each game-specific page; re-verified with a plain `requests.get` (HTTP 200,
no JS/API needed - classic server-rendered HTML). Each page's default view ("Past 3 Drawings")
is enough for this project's cumulative-merge scraping model (every run only needs to add any
draws since the last run).

Real confirmed rules (from each game's own JackpotGames/<Name>.aspx page, all re-verified this
session, not assumed):
  - Lotto: 6 of 1-49, "$1 for two plays" (i.e. 2 lines per $1), Mon/Wed/Sat 8pm PT. Fixed sub-
    jackpot tiers (5of6=$1,000, 4of6=$30, 3of6=$3); only the 6of6 tier is the real pari-mutuel
    jackpot.
  - Hit 5: 5 of 1-42, $1/play, nightly 8pm PT. 2-of-5 wins a free ticket (non-cash, not modeled).
  - Match 4: 4 of 1-24, $2/play, nightly 8pm PT - genuinely FIXED prizes at every tier (4of4=
    $10,000, 3of4=$20, 2of4=$2), confirmed real ("doesn't divide among multiple winners and
    doesn't accumulate between drawings") - unlike most of this project's other "4 of N" games.
  - Pick 3: 3 digits, 0-9, nightly 8pm PT, no Fireball-style bonus digit (confirmed absent, unlike
    some other states' Pick 3).
  - Cash Pop: 1 of 1-15, nightly 8pm PT (a single daily drawing - NOT the 5-sessions/day shape
    some other states' Cash Pop has, confirmed per-state rather than assumed). Same random-
    per-ticket-multiplier mechanic as other states' Cash Pop (not computable from the public
    draw feed beyond win/loss).
"""
import datetime as dt
import json
import logging
import re
import subprocess
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("wa_scraper")

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
PAGE_URL = "https://walottery.com/WinningNumbers/PastDrawings.aspx"
OUTPUT_DIR = Path("data") / "washington"
MAX_HISTORY = 100

DATE_NUMS_RE = re.compile(r"([A-Za-z]{3}, [A-Za-z]{3} \d{1,2}, \d{4}) ((?:\d+ ){1,})Prize")


def mdy_text_to_iso(text: str) -> str:
    d = dt.datetime.strptime(text, "%a, %b %d, %Y")
    return d.strftime("%Y-%m-%d")


def fetch_html(url: str) -> str:
    # walottery.com's TLS config rejects some Python/urllib3+LibreSSL builds'
    # ClientHello (TLSV1_ALERT_PROTOCOL_VERSION) even though a plain `curl` with the
    # same UA succeeds - confirmed this session. Shelling out to curl sidesteps that
    # incompatibility and works identically in both this project's dev sandbox and
    # GitHub Actions' runner.
    result = subprocess.run(
        ["curl", "-sL", "-A", UA, "--max-time", "20", url],
        capture_output=True, text=True, check=True,
    )
    return result.stdout


def fetch_rows(game_id: str) -> list[tuple[str, list[int]]]:
    from bs4 import BeautifulSoup
    html = fetch_html(f"{PAGE_URL}?gamename={game_id}")
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    out = []
    for date_text, nums_text in DATE_NUMS_RE.findall(text):
        out.append((mdy_text_to_iso(date_text), [int(x) for x in nums_text.split()]))
    return out


def save(subdir: str, entries: list[dict]):
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
    seen = {e["draw_date"] for e in entries}
    merged = entries + [e for e in existing if e.get("draw_date") not in seen]
    out_path.write_text(json.dumps(merged[:MAX_HISTORY], ensure_ascii=False, indent=2))
    log.info("saved %s (%d entries)", out_path, min(len(merged), MAX_HISTORY))


def scrape_numbers(game_id: str, subdir: str, expected_count: int, field: str = "numbers"):
    out = []
    for draw_date, nums in fetch_rows(game_id):
        if len(nums) != expected_count:
            continue
        out.append({
            "draw_date": draw_date, field: nums,
            "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "source_url": f"{PAGE_URL}?gamename={game_id}",
        })
    save(subdir, out)
    log.info("%s: %d entries, latest %s", subdir, len(out), out[0] if out else None)


PICK3_RE = re.compile(r"([A-Za-z]{3}, [A-Za-z]{3} \d{1,2}, \d{4}) (\d) (\d) (\d)\b")


def scrape_pick3():
    html = fetch_html(f"{PAGE_URL}?gamename=pick3")
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    idx = text.find("Winning Numbers")
    text = text[idx:] if idx != -1 else text

    by_date: dict[str, list[int]] = {}
    for date_text, d1, d2, d3 in PICK3_RE.findall(text):
        draw_date = mdy_text_to_iso(date_text)
        by_date.setdefault(draw_date, [int(d1), int(d2), int(d3)])

    out = [
        {
            "draw_date": draw_date, "digits": digits,
            "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "source_url": f"{PAGE_URL}?gamename=pick3",
        }
        for draw_date, digits in by_date.items()
    ]
    save("pick3", out)
    log.info("pick3: %d entries, latest %s", len(out), out[0] if out else None)


def scrape_cashpop():
    out = []
    for draw_date, nums in fetch_rows("cashpop"):
        if len(nums) != 1:
            continue
        out.append({
            "draw_date": draw_date, "number": nums[0],
            "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "source_url": f"{PAGE_URL}?gamename=cashpop",
        })
    save("cashpop", out)
    log.info("cashpop: %d entries, latest %s", len(out), out[0] if out else None)


def main():
    errors = []
    jobs = [
        ("lotto", lambda: scrape_numbers("lotto", "lotto", 6)),
        ("hit5", lambda: scrape_numbers("hit5", "hit5", 5)),
        ("match4", lambda: scrape_numbers("match4", "match4", 4)),
        ("pick3", scrape_pick3),
        ("cashpop", scrape_cashpop),
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
