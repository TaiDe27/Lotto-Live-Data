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
"""
import dataclasses
import datetime as dt
import json
import logging
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ma_scraper")

URL = "https://www.masslottery.com/api/v1/draw-results"
USER_AGENT = "LottoLiveApp/0.1 (+mailto:YOUR-CONTACT-EMAIL@example.com)"
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
