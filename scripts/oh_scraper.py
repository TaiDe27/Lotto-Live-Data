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

2026-11-10 update: each `cmsNumbers` game object also carries a sibling `NextDrawDate` field
(ISO datetime with offset, e.g. "2026-11-10T19:05:00-05:00") alongside `Draws` — but verified live
(via a throwaway debug-logging branch dispatched through the real `scrape-oh.yml` workflow, since
this field isn't visible from `Draws` alone) that it is only genuinely POPULATED for `rollingcash5`;
`ClassicLotto`, `Pick3`, `Pick4`, and `Pick5` all return the literal .NET null-date sentinel
"0001-01-01T00:00:00" for this field — a real gap on ohiolottery.com's own data, not a scraping
gap, so those five stay without next-draw-date capture from this source. No jackpot/next-jackpot-
estimate field was found anywhere in the non-Draws payload for any game (Rolling Cash 5's only
jackpot-adjacent figure is each past draw's own `PrizePool`, which is the AWARDED amount for that
already-drawn jackpot, not a forward-looking next-draw estimate — using it as such would require
re-deriving pari-mutuel rollover math this project doesn't model, so nextJackpot/nextCashValue stay
"none" for Rolling Cash 5 too).

2026-11-13 update (Classic Lotto + Pick 3/4/5 next-draw wiring): re-confirmed the above gap is
still live today (production `latest.json` for classic-lotto/pick3/pick4/pick5 fetched via the real
`scrape-oh.yml` run this morning carries no `next_draw_date`, while the same run's rolling-cash5
entry does) — ohiolottery.com itself is also presently behind an active Cloudflare "Attention
Required!" challenge from this machine's network path (both a direct `requests` call and a separate
fetch-tool call got 403"ed), so no fresh fetch of it was possible this round; relying on that
same-day production snapshot as the live check instead of a redundant fetch.
  - Classic Lotto: it DOES have a real jackpot (unlike the digit games) and IS covered by
    lotteryusa.com's `.c-state-short-info` "Next draw" widget (confirmed live via
    `lu.parse_next_draw_info` just now: "Next Draw Date: Tomorrow, Nov 14, 2026" / "Next est.
    jackpot $5.5 Million" — correctly Mon/Wed/Sat-aware, Nov 13 2026 is a Friday) — the same widget
    NJ/NC already lean on via this shared helper for their own ohiolottery.com-shaped gaps. No
    cash-value row exists on lotteryusa.com for any game (see `lotteryusa_shared.parse_next_draw_info`'s
    own docstring), so Classic Lotto's nextCashValue stays "none".
  - Pick 3/4/5 (all three, both sessions): digit games have no jackpot concept, and the official
    source still doesn't carry next-draw-date — but the real schedule is fixed and already confirmed
    in this module's text above ("midday draw is at 12:29 p.m. and the evening draw is at 7:29 p.m.
    ... every day"), so `next_session_draw_date()` below computes each session's own next
    occurrence deterministically instead of scraping it (each session saved to its own
    `latest.json`, so no shared/session-lookup value is needed — simpler than
    `il_pick3_scraper.py`/`in_shared.py.next_draw()`'s single-shared-file session split).
"""
import datetime as dt
import json
import logging
import re
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

import lotteryusa_shared as lu

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("oh_scraper")

OUTPUT_DIR = Path("data") / "ohio"
MAX_HISTORY = 100
DRAW_GAMES_URL = "https://www.ohiolottery.com/games/draw-games"
MIDDAY_HOUR_CUTOFF = 15  # ImportedOn hour < this => midday session, else evening

CLASSIC_LOTTO_LOTTERYUSA_URL = "https://www.lotteryusa.com/ohio/classic-lotto/"

# Pick 3/4/5 share one fixed daily schedule, confirmed on lotteryusa.com's own Ohio game pages:
# "the midday draw is at 12:29 p.m. and the evening draw is at 7:29 p.m." — every day, no days
# skipped (the module docstring's small Saturday-evening Cash Explosion shift is already-accepted
# slop, same tolerance this project gives similar minor variances elsewhere).
OH_TZ = ZoneInfo("America/New_York")
DIGIT_MIDDAY_DRAW_TIME = dt.time(12, 29)
DIGIT_EVENING_DRAW_TIME = dt.time(19, 29)


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


NULL_NEXT_DRAW_DATE_SENTINEL = "0001-01-01"


def next_draw_date(game: dict) -> "str | None":
    """Extracts the game-level `NextDrawDate` sibling field (see module docstring's 2026-11-10
    update) as a plain "yyyy-MM-dd" date, or None if the source left it at its .NET null-date
    sentinel (confirmed the case for ClassicLotto/Pick3/Pick4/Pick5 — a real source-side gap)."""
    raw = game.get("NextDrawDate")
    if not raw or raw.startswith(NULL_NEXT_DRAW_DATE_SENTINEL):
        return None
    return raw.split("T")[0]


def fetch_classic_lotto_next_draw_info() -> dict:
    """Classic Lotto's official-source `NextDrawDate` is the confirmed .NET null-date sentinel (see
    `next_draw_date()` above), and no jackpot/next-jackpot field exists anywhere in the cmsNumbers
    payload for any game (module docstring). Unlike the digit games, Classic Lotto DOES have a real
    growing jackpot, and lotteryusa.com's `.c-state-short-info` "Next draw" widget on its own
    Classic Lotto page carries both pieces (verified live 2026-11-13, see module docstring's
    2026-11-13 update) — reusing the same `lu.parse_next_draw_info` helper NJ/NC already lean on for
    their own ohiolottery.com-shaped gaps. Returns
    {"next_draw_date": "yyyy-MM-dd" | None, "next_jackpot_amount": raw "$X" text | None}; failure to
    fetch degrades to both None rather than raising, so a lotteryusa.com hiccup doesn't also take
    down Classic Lotto's own real draw-history save."""
    try:
        html = lu.fetch_html(CLASSIC_LOTTO_LOTTERYUSA_URL)
    except Exception as e:  # noqa: BLE001
        log.warning("classic-lotto 的 lotteryusa.com next-draw 信息抓取失败: %s", e)
        return {"next_draw_date": None, "next_jackpot_amount": None}
    return lu.parse_next_draw_info(html)


def extract_pool_game(
    game: dict,
    include_next_draw_date: bool = False,
    extra_next_draw_date: "str | None" = None,
    extra_next_jackpot_amount: "str | None" = None,
) -> list[dict]:
    out = []
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    next_date = next_draw_date(game) if include_next_draw_date else extra_next_draw_date
    for draw in game.get("Draws") or []:
        entry = {
            "draw_date": to_draw_date(draw), "numbers": draw_values(draw),
            "fetched_at": now, "source_url": DRAW_GAMES_URL,
        }
        if next_date:
            entry["next_draw_date"] = next_date
        if extra_next_jackpot_amount:
            entry["next_jackpot_amount"] = extra_next_jackpot_amount
        out.append(entry)
    return out


def next_session_draw_date(draw_time: dt.time, now: "dt.datetime | None" = None) -> str:
    """Single-session-per-day next draw date: if local (Eastern) now is before today's draw time,
    the next draw is today; otherwise it's tomorrow. Mirrors `mi_scraper.py`'s own
    `next_draw_date()` helper for single-draw-per-day games — applied here once per Pick 3/4/5
    SESSION (not once for the combined midday+evening schedule): unlike AZ/IL's one-shared-file-
    plus-sessionFilter approach, Ohio's own `save_results` already writes midday and evening to two
    separate `latest.json` files, so each file's own "next draw" must always be a same-session
    occurrence — stamping a single shared "whichever session comes next" value onto both files
    would wrongly tell the midday file its next draw is tonight's evening draw (or vice versa)."""
    now = dt.datetime.now(OH_TZ) if now is None else now.astimezone(OH_TZ)
    today = now.date()
    draw_dt = dt.datetime.combine(today, draw_time, tzinfo=OH_TZ)
    if now < draw_dt:
        return today.isoformat()
    return (today + dt.timedelta(days=1)).isoformat()


def extract_digit_game_split(game: dict) -> tuple[list[dict], list[dict]]:
    midday, evening = [], []
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    next_midday_date = next_session_draw_date(DIGIT_MIDDAY_DRAW_TIME)
    next_evening_date = next_session_draw_date(DIGIT_EVENING_DRAW_TIME)
    for draw in game.get("Draws") or []:
        entry = {
            "draw_date": to_draw_date(draw), "digits": draw_values(draw),
            "fetched_at": now, "source_url": DRAW_GAMES_URL,
        }
        imported_on = draw.get("ImportedOn") or ""
        hour = int(imported_on.split("T")[1].split(":")[0]) if "T" in imported_on else 12
        if hour < MIDDAY_HOUR_CUTOFF:
            entry["next_draw_date"] = next_midday_date
            midday.append(entry)
        else:
            entry["next_draw_date"] = next_evening_date
            evening.append(entry)
    return midday, evening


def main():
    errors = []
    games_by_id = {g["StringIdentifier"]: g for g in fetch_cms_numbers()}
    classic_lotto_next_info = fetch_classic_lotto_next_draw_info()

    jobs = [
        ("classic-lotto", lambda: extract_pool_game(
            games_by_id["ClassicLotto"],
            extra_next_draw_date=classic_lotto_next_info.get("next_draw_date"),
            extra_next_jackpot_amount=classic_lotto_next_info.get("next_jackpot_amount"),
        )),
        ("rolling-cash5", lambda: extract_pool_game(games_by_id["rollingcash5"], include_next_draw_date=True)),
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
