"""
Ohio own-games — sourced directly from ohiolottery.com's official draw-games listing page
(https://www.ohiolottery.com/games/draw-games). Replaces the earlier lotteryusa.com fallback
(2026-10-16): the official page returns 200 via plain requests (no Cloudflare/WAF block, no
robots.txt restriction — robots.txt 404s entirely on this domain), and while the page itself is a
client-side Vue shell (no visible draw numbers in the rendered HTML text), it embeds the FULL real
draw payload for every draw game server-side in a hidden
`<input id="cmsNumbers" value='[...]'>` element's `value` attribute — one JSON object per game,
each with a `Draws` array (DrawDate, Numbers w/ Position, Prizes, ImportedOn/ApprovedOn). This is
strictly fresher and simpler than both the old lotteryusa.com fallback and a separately-discovered
authenticated api-solutions.ohiolottery.com API (which needs a login flow with a hardcoded frontend
credential) — no auth needed at all for this page.

Confirmed mechanics (unchanged from before):
  - Classic Lotto: 6 of 1-49, $1/play. Real stated schedule (lotteryusa.com/ohio/classic-lotto/,
    cross-checked since ohiolottery.com's own page text is JS-rendered and not visible to a plain
    fetch): "Draws occur Monday, Wednesday, and Saturday at approximately 7:05 p.m." Has an
    optional "Kicker" add-on (separately-purchased $1 ticket, 6 digits compared position-by-
    position against a drawn 6-digit string) — a genuinely different mechanic from every other
    add-on already modeled in this project; NOT built this round, same documented-gap treatment as
    MS Match5's Multiplier/MO Millions' EZ Match/NJ's XTRA.
  - Rolling Cash 5: 5 of 1-39, $1/play, no special ball. Real stated schedule: "Draws occur every
    day at approximately 7:05 p.m." (lotteryusa.com/ohio/rolling-cash-5/).
  - Pick 3 / Pick 4 / Pick 5: standard digit games. Real stated schedule (lotteryusa.com/ohio/
    pick-3/, pick-4/, pick-5/ and their midday- counterparts): "the midday draw is at 12:29 p.m.
    and the evening draw is at 7:29 p.m. On Saturday evening, the draw occurs between 7:45 p.m.
    and 8:00 p.m. during the Cash Explosion televised show" — the Saturday shift isn't separately
    modeled as its own GAMES entry (small weekly variance, scraper doesn't care about exact draw
    time, and the existing multi-offset safety net still catches it on a later recheck).
    `cmsNumbers`'s Pick3/4/5 `Draws` entries aren't labeled midday/evening explicitly — split here
    by the `ImportedOn` hour: real midday imports land ~12:44-12:45, real evening imports ~19:46-
    19:49 (verified against several consecutive days), so hour < 15 => midday, else => evening.
  - No Fireball-equivalent add-on confirmed for Ohio's digit games.

Deliberately NOT built: KENO (confirmed ~4-minute draws, ~245/day — far over this project's
standing "≤10 draws/day" cutoff, same class as Missouri's Club Keno).
"""
import datetime as dt
import json
import logging
import re
from pathlib import Path

import requests

import lotteryusa_shared as lu

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("oh_scraper")

OUTPUT_DIR = Path("data") / "ohio"
MAX_HISTORY = 100
DRAW_GAMES_URL = "https://www.ohiolottery.com/games/draw-games"
MIDDAY_HOUR_CUTOFF = 15  # ImportedOn hour < this => midday session, else evening


def save_results(subdir: str, new_entries: list[dict]) -> Path:
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
    merged = lu.dedup_merge(existing, new_entries, "draw_date", MAX_HISTORY)
    out_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2))
    log.info("已保存: %s (%d 条记录)", out_path, len(merged))
    return out_path


def fetch_cms_numbers() -> list[dict]:
    resp = requests.get(DRAW_GAMES_URL, headers={"User-Agent": lu.USER_AGENT}, timeout=lu.REQUEST_TIMEOUT_SEC)
    resp.raise_for_status()
    m = re.search(r"id=\"cmsNumbers\" value='(\[.*?\])'\s*>", resp.text, re.S)
    if not m:
        raise ValueError("cmsNumbers 没找到，页面结构可能变了")
    return json.loads(m.group(1))


def draw_values(draw: dict) -> list[int]:
    numbers = sorted(draw.get("Numbers") or [], key=lambda n: n["Position"])
    return [n["Value"] for n in numbers]


def to_draw_date(draw: dict) -> str:
    # "2026-10-15T00:00:00" -> "2026-10-15"
    return draw["DrawDate"].split("T")[0]


def extract_pool_game(game: dict) -> list[dict]:
    out = []
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    for draw in game.get("Draws") or []:
        out.append({
            "draw_date": to_draw_date(draw), "numbers": draw_values(draw),
            "fetched_at": now, "source_url": DRAW_GAMES_URL,
        })
    return out


def extract_digit_game_split(game: dict) -> tuple[list[dict], list[dict]]:
    midday, evening = [], []
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    for draw in game.get("Draws") or []:
        entry = {
            "draw_date": to_draw_date(draw), "digits": draw_values(draw),
            "fetched_at": now, "source_url": DRAW_GAMES_URL,
        }
        imported_on = draw.get("ImportedOn") or ""
        hour = int(imported_on.split("T")[1].split(":")[0]) if "T" in imported_on else 12
        (midday if hour < MIDDAY_HOUR_CUTOFF else evening).append(entry)
    return midday, evening


def main():
    errors = []
    games_by_id = {g["StringIdentifier"]: g for g in fetch_cms_numbers()}

    jobs = [
        ("classic-lotto", lambda: extract_pool_game(games_by_id["ClassicLotto"])),
        ("rolling-cash5", lambda: extract_pool_game(games_by_id["rollingcash5"])),
    ]
    for subdir, fn in jobs:
        try:
            entries = fn()
            if not entries:
                raise ValueError("没抓到任何记录")
            save_results(subdir, entries)
            log.info("%s: 最新一条 %s", subdir, entries[0])
        except Exception as e:  # noqa: BLE001
            log.error("%s 失败: %s", subdir, e)
            errors.append((subdir, e))

    for game_id, label in [("Pick3", "pick3"), ("Pick4", "pick4"), ("Pick5", "pick5")]:
        try:
            midday, evening = extract_digit_game_split(games_by_id[game_id])
            if not midday and not evening:
                raise ValueError("没抓到任何记录")
            if midday:
                save_results(f"{label}-midday", midday)
                log.info("%s-midday: 最新一条 %s", label, midday[0])
            if evening:
                save_results(f"{label}-evening", evening)
                log.info("%s-evening: 最新一条 %s", label, evening[0])
        except Exception as e:  # noqa: BLE001
            log.error("%s 失败: %s", label, e)
            errors.append((label, e))

    if errors:
        raise RuntimeError(f"{len(errors)} 个游戏抓取失败: {errors}")


if __name__ == "__main__":
    main()
