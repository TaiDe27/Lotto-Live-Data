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
  - Cash Pop (gameSelector=cp) is NOT built — confirmed from oregonlottery.org's own page:
    "Drawings: Hourly from 7 AM — 10 PM ... you have 16 drawings per day to play" — 16/day
    exceeds this project's standing ≤10-draws/day cutoff. The gameSelector was fetched and
    verified during research (confirming `cp` = Cash Pop) but deliberately not wired into a
    scraper function.

API response shape per gameSelector: `{"OpenDraw": {...not yet drawn...}, "ClosedDraw": {...most
recent completed draw...}}`, both sharing the same `WinningNumbers` (10-slot, zero-padded) /
`DrawDateTime` / `Multiplier` fields — only `ClosedDraw` is used here.
"""
import datetime as dt
import json
import logging
import time
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


def fetch_draw(game_selector: str) -> dict:
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
            return closed
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("第 %d 次尝试失败 (%s): %s", attempt, game_selector, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SEC * attempt)
    raise RuntimeError(f"重试多次后仍然失败: {game_selector}") from last_err


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
            closed = fetch_draw(selector)
            draw_date = closed["DrawDateTime"][:10]
            nums = [n for n in closed["WinningNumbers"] if n][:count]
            entry = {
                "draw_date": draw_date, "numbers": nums,
                "next_jackpot_amount": closed.get("EstimatedJackpot"),
                "draw_number": closed.get("DrawNumber"), "fetched_at": now, "source_url": API_URL,
            }
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
        entry = {
            "draw_date": draw_date, "session": session, "digits": digits,
            "draw_number": closed.get("DrawNumber"), "fetched_at": now, "source_url": API_URL,
        }
        save_entries("pick4", [entry], ("draw_date", "session"))
    except Exception as e:  # noqa: BLE001
        log.error("pick4 失败: %s", e)
        errors.append(("pick4", e))

    if errors:
        raise RuntimeError(f"{len(errors)} 个游戏抓取失败: {errors}")


if __name__ == "__main__":
    main()
