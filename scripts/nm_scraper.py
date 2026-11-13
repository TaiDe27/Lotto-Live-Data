"""
New Mexico own-games — nmlottery.com's own pages embed a results widget hosted at
`nmlotterydynamic.sks.com` (their lottery systems vendor, SCIQ/SKS), found by reading the page's own
embedded iframe `src` rather than guessing — a plain, server-rendered page, no JS execution needed:
`https://nmlotterydynamic.sks.com/rts/games/pastdrawings/search/?game=<id>` returns up to ~100 past
drawings in one request (`<h3>MM/DD/YYYY SESSION</h3><ul class="winning-numbers">...</ul>` rows).

Confirmed real game IDs on this widget (from its own dropdown, which also lists retired games —
`hotlotto`, `luckynumbers` — and legacy `pick3`/`pick4` IDs alongside the CURRENT `pick3plus`/
`pick4plus` ones; nmlottery.com's own current navigation only surfaces "Pick 3 Plus"/"Pick 4 Plus"
as the live products, so only those two are scraped here):
  - `pick3plus` / `pick4plus`: standard digit games, Day 1:00pm / Evening 9:30pm MT. "Plus" refers
    to optional Front-Pair/Back-Pair side bets, not a Fireball-style drawn add-on digit — confirmed
    no extra/replacement digit anywhere in this widget's rendering.
  - `roadrunnercash`: 5 of 1-37, $1/play, daily 9:30pm MT, no special ball. The pastdrawings widget
    itself has no live jackpot field (confirmed again 2026-11-11: its HTML is pure ASP.NET
    VIEWSTATE-encoded past-results markup, nothing else) — but nmlottery.com's own homepage calls a
    SEPARATE, real JSON webservice to render its jackpot ticker: `POST
    https://nmlotterydynamic.sks.com/rts/Services/WebServicesHomePage.aspx/GetJackpotList` (same sks.com
    vendor host, `Content-Type: application/json`, empty `{}` body) returns
    `{"d": {"roadrunnercash": {"gametype": "roadrunnercash", "jackpotAmount": null,
    "jackpotDate": "11/10/2026", "estimatedCashOption": "207,000"}, ...}}` — confirmed live, cross-
    checked against the homepage's own rendered `.RoadRunnerCashAmount`/`.RoadRunnerCashDate` text.
    `jackpotAmount` is always null for this game (it's a single lump-sum game, no annuity option —
    `estimatedCashOption` IS the real jackpot figure, not a separate cash value) and `jackpotDate` is
    the next draw date. This same endpoint has no `pick3plus`/`pick4plus` keys at all (only
    jackpot-pool games: powerball/megamillions/hotlotto/lottoamerica/roadrunnercash/
    redhotjackpotbingo), and `GetDrawNumbersList` (its sibling endpoint, past-winning-numbers only)
    has no `pick3plus`/`pick4plus` keys either (only legacy `pick3`/`pick4`, already-drawn) — so
    Pick 3 Plus/Pick 4 Plus genuinely have no live next-draw-date source anywhere on this site;
    confirmed gap, left unmapped.

New Mexico observes Mountain Time WITH DST (`America/Denver`), unlike Arizona's fixed-offset zone.
"""
import datetime as dt
import json
import logging
import re
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("nm_scraper")

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
REQUEST_TIMEOUT_SEC = 15
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 5
OUTPUT_DIR = Path("data") / "new-mexico"
MAX_HISTORY = 100

ROW_RE = re.compile(
    r'<h3>(\d{1,2}/\d{1,2}/\d{4})\s*([A-Z]*)</h3>\s*<ul class="winning-numbers">(.*?)</ul>',
    re.S,
)
BALL_RE = re.compile(r"<li>(\d+)</li>")


def _get(url: str) -> str:
    import requests
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_SEC)
            resp.raise_for_status()
            return resp.text
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("第 %d 次尝试失败 (%s): %s", attempt, url, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SEC * attempt)
    raise RuntimeError(f"重试多次后仍然失败: {url}") from last_err


def fetch_rows(game_id: str) -> list[dict]:
    url = f"https://nmlotterydynamic.sks.com/rts/games/pastdrawings/search/?game={game_id}"
    html = _get(url)
    out = []
    for m in ROW_RE.finditer(html):
        date_str, session, balls_html = m.groups()
        month, day, year = date_str.split("/")
        draw_date = dt.date(int(year), int(month), int(day)).isoformat()
        numbers = [int(b) for b in BALL_RE.findall(balls_html)]
        if not numbers:
            continue
        out.append({
            "draw_date": draw_date,
            "session": session.strip() or None,
            "numbers": numbers,
            "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "source_url": url,
        })
    if not out:
        raise ValueError(f"没解析到任何记录 (game={game_id})")
    return out[:MAX_HISTORY]


def fetch_jackpot_info(game_id: str) -> dict:
    """Calls the sks.com homepage jackpot webservice directly (same host as `fetch_rows`, different
    endpoint — see module doc). Returns {"next_draw_date": "yyyy-MM-dd"|None,
    "next_jackpot_amount": raw "N,NNN,NNN" text|None}. Caught and treated as best-effort by the
    caller: a failure here must never take down the main numbers fetch for the same game."""
    import requests
    url = "https://nmlotterydynamic.sks.com/rts/Services/WebServicesHomePage.aspx/GetJackpotList"
    resp = requests.post(
        url,
        headers={"User-Agent": USER_AGENT, "Content-Type": "application/json; charset=UTF-8"},
        data="{}",
        timeout=REQUEST_TIMEOUT_SEC,
    )
    resp.raise_for_status()
    game = resp.json().get("d", {}).get(game_id)
    if not game:
        return {"next_draw_date": None, "next_jackpot_amount": None}
    next_draw_date = None
    raw_date = game.get("jackpotDate")
    if raw_date:
        try:
            month, day, year = raw_date.split("/")
            next_draw_date = dt.date(int(year), int(month), int(day)).isoformat()
        except (ValueError, AttributeError):
            next_draw_date = None
    return {
        "next_draw_date": next_draw_date,
        # Single lump-sum game (no annuity option): estimatedCashOption IS the jackpot figure,
        # jackpotAmount is always null for it — confirmed live, see module doc.
        "next_jackpot_amount": game.get("estimatedCashOption"),
    }


def save_results(subdir: str, new_entries: list[dict], key_fields: tuple[str, ...]) -> Path:
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
    seen = {tuple(e.get(k) for k in key_fields) for e in new_entries}
    merged = new_entries + [e for e in existing if tuple(e.get(k) for k in key_fields) not in seen]
    merged = merged[:MAX_HISTORY]
    out_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2))
    log.info("已保存: %s (%d 条记录)", out_path, len(merged))
    return out_path


def main():
    errors = []

    for subdir, game_id in [("pick3plus", "pick3plus"), ("pick4plus", "pick4plus")]:
        try:
            rows = fetch_rows(game_id)
            save_results(subdir, rows, ("draw_date", "session"))
            log.info("%s: 最新一条 %s", subdir, rows[0])
        except Exception as e:  # noqa: BLE001
            log.error("%s 失败: %s", subdir, e)
            errors.append((subdir, e))
        time.sleep(1)

    try:
        rows = fetch_rows("roadrunnercash")
        try:
            jackpot_info = fetch_jackpot_info("roadrunnercash")
        except Exception as e:  # noqa: BLE001 - best-effort only, never fails the numbers fetch
            log.warning("roadrunner-cash 的 next-draw/jackpot 信息抓取失败（不影响开奖号码保存): %s", e)
            jackpot_info = {"next_draw_date": None, "next_jackpot_amount": None}
        for row in rows:
            row["next_draw_date"] = jackpot_info["next_draw_date"]
            row["next_jackpot_amount"] = jackpot_info["next_jackpot_amount"]
        save_results("roadrunner-cash", rows, ("draw_date",))
        log.info("roadrunner-cash: 最新一条 %s", rows[0])
    except Exception as e:  # noqa: BLE001
        log.error("roadrunner-cash 失败: %s", e)
        errors.append(("roadrunner-cash", e))

    if errors:
        raise RuntimeError(f"{len(errors)} 个游戏抓取失败: {errors}")


if __name__ == "__main__":
    main()
