"""
Massachusetts own-games — masslottery.com's real public JSON API
(https://www.masslottery.com/api/v1/draw-results), confirmed 2026-10-14 reachable with this
project's identifying UA, no browser-UA workaround needed (unlike several other states this round).
One fetch returns the latest draw for every active game at once (the `?game=` query param appears
to be ignored/vestigial — confirmed it returns the full set regardless), same "one call, everything"
shape as fl_draw_games_scraper.py.

Keno and Wheel of Luck are NOT in this response (checked) — deferred, not guessed; this endpoint only
surfaces Mega Millions/Powerball/Megabucks/Mass 3/Mass 4/The Numbers Game/Mass Cash/Lucky for Life/
Millionaire for Life/mega_millions in the confirmed sample. Megabucks here is Massachusetts' OWN
6/44 game — distinct from Maine/NH/VT's shared "Tri-State Megabucks Plus" (not the same game,
despite the similar name — do not merge these two game catalogs).

2026-11-13 re-check: re-fetched this same API fresh — mass_cash is STILL genuinely absent from
`estimatedJackpot` (reconfirmed, not a stale earlier read). But Mass Cash's real top prize is a
FIXED $100,000 (confirmed via masslottery.com's own game description + corroborated independently
by lotteryusa.com/jackpot.com/lottery.net: "$100,000 top prize... fixed... does not roll over") —
same class of fix as Maine Gimme 5's fixed $100k. Wired as a constant `next_jackpot_amount` on every
mass_cash entry. No cash-option field: this is a single lump-sum prize, not an annuity, so there is
no separate cash value to report. `next_draw_date` is computed locally from the confirmed fixed
2:00 PM / 9:00 PM ET schedule (same `drawSchedule` already in config.json) — the live API has no
forward-looking field for this game, so this mirrors mo_scraper.py's/ms_scraper.py's existing
`compute_next_draw`-for-known-fixed-schedule pattern rather than guessing a live source that
doesn't exist.
"""
import dataclasses
import datetime as dt
import json
import logging
import time
from pathlib import Path
from zoneinfo import ZoneInfo

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ma_scraper")

URL = "https://www.masslottery.com/api/v1/draw-results"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
REQUEST_TIMEOUT_SEC = 15
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 5
OUTPUT_DIR = Path("data") / "massachusetts"

# gameIdentifier -> (output subdir, has own-session-split-via-drawSequence)
OWN_GAMES = {
    "mass_3": "mass3",
    "mass_4": "mass4",
    "the_numbers_game": "numbersgame",
    "mass_cash": "masscash",
    "megabucks": "megabucks",
}

# Mass Cash's real top prize is a fixed, non-progressive $100,000 (confirmed 2026-11-13, see module
# doc) — not a live-changing jackpot, so this is a constant, not something fetched per-run.
MASSCASH_FIXED_JACKPOT = 100_000
EASTERN_TZ = ZoneInfo("America/New_York")
MASSCASH_MIDDAY_TIME = dt.time(14, 0)
MASSCASH_EVENING_TIME = dt.time(21, 0)


def _compute_next_two_session_draw(now_local: dt.datetime, midday_time: dt.time, evening_time: dt.time) -> str:
    """Same two-session next-draw logic as mo_scraper.py's/ms_scraper.py's `compute_next_draw`,
    returning just the ISO date string (Mass Cash's session is already implied by the subdir this
    caller writes to, unlike those digit games which tag `next_session` onto the entry too)."""
    today = now_local.date()
    midday_dt = dt.datetime.combine(today, midday_time, tzinfo=now_local.tzinfo)
    evening_dt = dt.datetime.combine(today, evening_time, tzinfo=now_local.tzinfo)
    if now_local < midday_dt or now_local < evening_dt:
        return today.isoformat()
    return (today + dt.timedelta(days=1)).isoformat()


def fetch() -> dict:
    import requests
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(URL, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_SEC)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("第 %d 次尝试失败: %s", attempt, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SEC * attempt)
    raise RuntimeError("重试多次后仍然失败") from last_err


def save(subdir: str, new_entries: list[dict], has_session: bool) -> None:
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
    if has_session:
        new_keys = {(e["draw_date"], e["session"]) for e in new_entries}
        history = [h for h in history if (h.get("draw_date"), h.get("session")) not in new_keys]
    else:
        new_keys = {e["draw_date"] for e in new_entries}
        history = [h for h in history if h.get("draw_date") not in new_keys]
    merged = (new_entries + history)[:100]
    out_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2))
    log.info("已保存: %s (%d 条，本次更新 %d 条)", out_path, len(merged), len(new_entries))


def main():
    data = fetch()
    rows = data.get("winningNumbers", [])
    log.info("API 返回 %d 条记录，gameIdentifier 分布: %s", len(rows),
              sorted({r.get("gameIdentifier") for r in rows}))

    # 2026-11-11: the SAME API response's top-level `estimatedJackpot` array carries each game's
    # real forward-looking next-draw date/jackpot/cash-option — confirmed live (megabucks IS one
    # of the 4 games in it; mass_cash/mass_3/mass_4/the_numbers_game are NOT, genuinely absent,
    # not missed — those stay without this field). Keyed by gameIdentifier, same key space as
    # `winningNumbers` above.
    next_draw_by_game = {
        item["gameIdentifier"]: item for item in data.get("estimatedJackpot", [])
        if item.get("gameIdentifier")
    }

    buckets: dict[str, list[dict]] = {v: [] for v in OWN_GAMES.values()}
    for r in rows:
        gid = r.get("gameIdentifier")
        if gid not in OWN_GAMES:
            continue
        subdir = OWN_GAMES[gid]
        nums = r.get("winningNumbers") or []
        extras = r.get("extras") or {}
        is_digit = subdir in ("mass3", "mass4", "numbersgame")
        entry = {
            "game": gid, "draw_date": r.get("drawDate"),
            ("digits" if is_digit else "numbers"): nums,
            "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(), "source_url": URL,
        }
        # the_numbers_game/mass_cash have 2 real draws/day (drawSequence 1/2) — tag by sequence as
        # a "midday"/"evening" session so the session-aware mapper can split them; mass_3/mass_4
        # only ever return 1 entry here (single real daily session), no session tag needed for those.
        if subdir in ("numbersgame", "masscash"):
            entry["session"] = "midday" if r.get("drawSequence") == 1 else "evening"
        if subdir == "megabucks" and r.get("jackpotAmount"):
            entry["jackpot_amount"] = str(r["jackpotAmount"])
        next_draw = next_draw_by_game.get(gid)
        if next_draw:
            entry["next_draw_date"] = next_draw.get("drawDateFor")
            if next_draw.get("estimatedJackpotUSD") is not None:
                entry["next_jackpot_amount"] = next_draw["estimatedJackpotUSD"]
            if next_draw.get("estimatedCashOptionUSD") is not None:
                entry["next_cash_value"] = next_draw["estimatedCashOptionUSD"]
        elif gid == "mass_cash":
            # Re-confirmed 2026-11-13: mass_cash is genuinely still absent from estimatedJackpot
            # (re-fetched fresh, not trusting the earlier read) — but its real top prize is a fixed
            # $100,000 and its draw schedule is a known fixed 2:00 PM / 9:00 PM ET, so both are
            # computed/constant here instead of left empty. See module doc.
            entry["next_jackpot_amount"] = MASSCASH_FIXED_JACKPOT
            entry["next_draw_date"] = _compute_next_two_session_draw(
                dt.datetime.now(EASTERN_TZ), MASSCASH_MIDDAY_TIME, MASSCASH_EVENING_TIME
            )
        buckets.setdefault(subdir, []).append(entry)

    for subdir, entries in buckets.items():
        if not entries:
            log.warning("[%s] 这次没有抓到任何记录", subdir)
            continue
        has_session = "session" in entries[0]
        save(subdir, entries, has_session)
    log.info("完成")


if __name__ == "__main__":
    main()
