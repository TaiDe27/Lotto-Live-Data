"""
Oregon own-games — sourced from the real official api2.oregonlottery.org backend, found via the
user's own browser devtools. Requires an `Ocp-Apim-Subscription-Key` header (Azure API Management
pattern, same class as Ohio's embedded login credential) — the key is a PUBLIC value embedded
directly in oregonlottery.org's own homepage HTML (`var olapi = {..., "apikey": "..."}`, served to
every visitor's browser), not a secret, confirmed by reading the page source directly.

Confirmed real draw times (quoted from oregonlottery.org's own game pages, 2026-10-16):
  - Megabucks (gameSelector=mb): "Every Monday, Wednesday & Saturday, 7:29 PM PT." 6 of 1-48.
  - Win for Life (gameSelector=wf): "Win For Life drawings occur every Monday, Wednesday, and
    Saturday at 7:30PM PT." 4 numbers between 1 and 77, no separate bonus ball.
  - Pick 4 (gameSelector=p4): "Drawings: Daily at 1 PM, 4PM, 7PM and 10 PM PT" — 4 sessions/day,
    standard digit game.
  - Cash Pop (gameSelector=cp): "Pick a number from 1 – 15 and if it matches the number drawn,
    you win!" / "DRAWINGS Hourly from 7 AM to 10 PM PT" — 16 sessions/day, genuinely exceeds this
    project's standing ≤10-draws/day cutoff, but the user explicitly reviewed this and asked to
    build it anyway (deliberate, informed exception for this one game — not a reason to relax the
    standing rule elsewhere). Real draw instants are :59 past the hour BEFORE the labeled session
    (confirmed via the API's own `RoundedDrawDateTime` field, e.g. a `DrawDateTime` of 21:59 has
    `RoundedDrawDateTime` 22:00 — the "10pm" session), giving 16 real clock times: 06:59, 07:59,
    08:59, 09:59, 10:59, 11:59, 12:59, 13:59, 14:59, 15:59, 16:59, 17:59, 18:59, 19:59, 20:59,
    21:59, all PT. Session label is derived from `RoundedDrawDateTime`'s hour (7am..10pm), not
    hardcoded per slot. `WinningNumbers` is the same 10-slot zero-padded array shape as the other
    games here, but only ONE slot is ever non-zero for Cash Pop (one number drawn, 1-15) — same
    `membershipPick` mechanic already built for many other states' Cash POP in this project.

API response shape per gameSelector: `{"OpenDraw": {...not yet drawn...}, "ClosedDraw": {...most
recent completed draw...}}`, both sharing the same `WinningNumbers` (10-slot, zero-padded) /
`DrawDateTime` / `Multiplier` fields — only `ClosedDraw` is used here.
"""
import datetime as dt
import json
import logging
import time
import zoneinfo
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("or_scraper")

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
REQUEST_TIMEOUT_SEC = 15
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 5
API_URL = "https://api2.oregonlottery.org/drawresults/MostRecentDraws"
SUBSCRIPTION_KEY = "683ab88d339c4b22b2b276e3c2713809"  # public, embedded in oregonlottery.org's own homepage HTML
OUTPUT_DIR = Path("data") / "oregon"
MAX_HISTORY = 100

# Pick 4 session times — re-confirmed via oregonlottery.org/jackpot/pick-4/ directly (same figures
# already quoted in this file's docstring, 2026-10-16): "Drawings: Daily at 1 PM, 4PM, 7PM and
# 10 PM PT" — genuinely 4 sessions every day, not a modeling error.
OR_TZ = zoneinfo.ZoneInfo("America/Los_Angeles")
PICK4_SESSION_TIMES = [("1pm", 13, 0), ("4pm", 16, 0), ("7pm", 19, 0), ("10pm", 22, 0)]


def compute_or_pick4_next_draw() -> tuple:
    """Returns (next_draw_date, next_session) for whichever Pick 4 session comes next from now."""
    now = dt.datetime.now(OR_TZ)
    candidates = []
    for session, hour, minute in PICK4_SESSION_TIMES:
        t = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if t <= now:
            t += dt.timedelta(days=1)
        candidates.append((t, session))
    next_time, next_session = min(candidates, key=lambda c: c[0])
    return next_time.date().isoformat(), next_session


def compute_or_cashpop_next_draw(session_hour: int) -> str:
    """Returns the ISO date on which this specific Cash Pop session (labeled by its rounded hour,
    7..22 i.e. 7am..10pm PT) will next occur, relative to now. Each session fires once/day, so this
    is "today" if that hour hasn't happened yet today, else "tomorrow" - same deterministic-
    schedule pattern as `compute_or_pick4_next_draw` above, generalized from 4 sessions to 16."""
    now = dt.datetime.now(OR_TZ)
    t = now.replace(hour=session_hour, minute=0, second=0, microsecond=0)
    if t <= now:
        t += dt.timedelta(days=1)
    return t.date().isoformat()


def fetch_both_draws(game_selector: str) -> tuple:
    """Returns (closed, open) - `OpenDraw` is the API's own real upcoming (not-yet-drawn) entry,
    sharing the same `DrawDateTime`/`EstimatedJackpot` shape as `ClosedDraw` (see module docstring).
    Used to populate genuinely forward-looking `next_draw_date`/`next_jackpot_amount` instead of
    reusing the just-completed `ClosedDraw`'s own (now-stale) estimate."""
    import requests

    headers = {"User-Agent": USER_AGENT, "Ocp-Apim-Subscription-Key": SUBSCRIPTION_KEY}
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(API_URL, params={"gameSelector": game_selector}, headers=headers, timeout=REQUEST_TIMEOUT_SEC)
            resp.raise_for_status()
            data = resp.json()
            closed = data.get("ClosedDraw")
            if not closed:
                raise ValueError(f"响应里没有 ClosedDraw: {list(data.keys())}")
            return closed, data.get("OpenDraw")
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("第 %d 次尝试失败 (%s): %s", attempt, game_selector, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SEC * attempt)
    raise RuntimeError(f"重试多次后仍然失败: {game_selector}") from last_err


def fetch_draw(game_selector: str) -> dict:
    closed, _ = fetch_both_draws(game_selector)
    return closed


def save_entries(subdir: str, new_entries: list, key_fields: tuple) -> Path:
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
    new_keys = {tuple(e.get(k) for k in key_fields) for e in new_entries}
    history = [h for h in history if tuple(h.get(k) for k in key_fields) not in new_keys]
    merged = (new_entries + history)[:MAX_HISTORY]
    out_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2))
    log.info("已保存: %s (%d 条记录)", out_path, len(merged))
    return out_path


def main():
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    errors = []

    for subdir, selector, count in [("megabucks", "mb", 6), ("win-for-life", "wf", 4)]:
        try:
            closed, open_draw = fetch_both_draws(selector)
            draw_date = closed["DrawDateTime"][:10]
            nums = [n for n in closed["WinningNumbers"] if n][:count]
            entry = {
                "draw_date": draw_date, "numbers": nums,
                "draw_number": closed.get("DrawNumber"), "fetched_at": now, "source_url": API_URL,
            }
            # `OpenDraw` is the API's own real next (not-yet-drawn) entry - confirmed live
            # 2026-11-10: Megabucks' OpenDraw.EstimatedJackpot is a genuine growing jackpot figure
            # (e.g. $2.3M), while Win for Life's is always 0.0 (it's a fixed $1,000/week-for-life
            # top prize, not a pari-mutuel jackpot - confirmed 0.0 on both ClosedDraw and OpenDraw,
            # not just a momentary zero) - so only Megabucks gets next_jackpot_amount.
            if open_draw and open_draw.get("DrawDateTime"):
                entry["next_draw_date"] = open_draw["DrawDateTime"][:10]
                if subdir == "megabucks":
                    next_jackpot = open_draw.get("EstimatedJackpot")
                    if next_jackpot:
                        entry["next_jackpot_amount"] = next_jackpot
            save_entries(subdir, [entry], ("draw_date",))
        except Exception as e:  # noqa: BLE001
            log.error("%s 失败: %s", subdir, e)
            errors.append((subdir, e))

    try:
        closed = fetch_draw("p4")
        draw_date = closed["DrawDateTime"][:10]
        draw_time = closed["DrawDateTime"][11:16]
        session_by_time = {"13:00": "1pm", "16:00": "4pm", "19:00": "7pm", "22:00": "10pm"}
        session = session_by_time.get(draw_time, draw_time)
        digits = [str(n) for n in closed["WinningNumbers"] if n][:4]
        next_draw_date, next_session = compute_or_pick4_next_draw()
        entry = {
            "draw_date": draw_date, "session": session, "digits": digits,
            "next_draw_date": next_draw_date, "next_session": next_session,
            "draw_number": closed.get("DrawNumber"), "fetched_at": now, "source_url": API_URL,
        }
        save_entries("pick4", [entry], ("draw_date", "session"))
    except Exception as e:  # noqa: BLE001
        log.error("pick4 失败: %s", e)
        errors.append(("pick4", e))

    try:
        closed = fetch_draw("cp")
        draw_date = closed["RoundedDrawDateTime"][:10]
        rounded_hour = int(closed["RoundedDrawDateTime"][11:13])
        session = f"{rounded_hour}am" if rounded_hour < 12 else ("12pm" if rounded_hour == 12 else f"{rounded_hour - 12}pm")
        nums = [n for n in closed["WinningNumbers"] if n]
        if len(nums) != 1:
            raise ValueError(f"Cash Pop 应为1个号码，抓到 {nums}")
        entry = {
            "draw_date": draw_date, "session": session, "number": str(nums[0]),
            "next_draw_date": compute_or_cashpop_next_draw(rounded_hour),
            "draw_number": closed.get("DrawNumber"), "fetched_at": now, "source_url": API_URL,
        }
        save_entries("cashpop", [entry], ("draw_date", "session"))
    except Exception as e:  # noqa: BLE001
        log.error("cashpop 失败: %s", e)
        errors.append(("cashpop", e))

    if errors:
        raise RuntimeError(f"{len(errors)} 个游戏抓取失败: {errors}")


if __name__ == "__main__":
    main()
