"""
Puerto Rico own-games — sourced from the real official backend API
(wordpresswebapi.azurewebsites.net/api/ElectronicDrawing), found via the user's own browser
devtools — PR Lottery's genuine backend, not a third party, no auth needed.

Confirmed real mechanics/schedule (quoted from loteriasdepuertorico.pr.gov's own Pega/Loto
Cash/Kino pages and FAQ, 2026-10-16):
  - Pega 2/3/4: "tienes 2 oportunidades diarias de lunes a sábado con sorteos diurnos a las 2pm y
    nocturnos a las 10pm" — Monday-Saturday ONLY (no Sunday draw), day 2:00 PM / night 10:00 PM
    Atlantic Standard Time (Puerto Rico observes no DST). Each has a real WILD BALL add-on digit
    ("un número adicional que se genera tras cada sorteo de Pega 2, Pega 3 y Pega 4... puede
    utilizar dicho número para sustituir cualquiera de los números ganadores") — the API's own
    `wildBall` field, same substitute-a-drawn-digit mechanic as Fireball/Wild Ball elsewhere in
    this project.
  - Loto Cash (formerly "Loto Plus"): "Elije cinco números entre el 1 y el 35... elije 1 número
    entre el 1 y el 10" — 5 of 1-35 + a separate "Plus" bonus ball 1-10. Draws Mon/Wed/Fri
    ("tres sorteos semanales... lunes, miércoles y viernes"). A per-draw Multiplier (2X-5X,
    "se selecciona al azar... tras cada sorteo de Loto Cash", multiplies non-jackpot prizes) is
    captured in the output (`multiplier` field) but not yet wired into Swift prize calculation
    this round. EXACT CLOCK TIME not found on the official site despite a real search (game page,
    FAQ, documents page all checked) — flagged as an open question, not guessed.
  - Revancha: "un sorteo adicional que se celebra con cada sorteo de Loto Cash los lunes,
    miércoles y viernes... te genera una combinación nueva de números de forma automática" — a
    genuinely separate drawing (its own real 5-of-35 + Plus numbers) held alongside every Loto
    Cash session, same pattern as Powerball Double Play. Same unconfirmed-exact-time caveat as
    Loto Cash.
  - Kino: confirmed "sorteos automáticos cada 4 minutos... de lunes a sábado, desde las 5:00 a.m.
    hasta las 2:00 a.m." — far over this project's standing ≤10 draws/day cutoff, NOT built.
  - Powerball/Double Play (gameIDs 10/14): already covered by this project's existing multistate
    Powerball/Powerball Double Play games — not duplicated here.

API gameIDs used: 2=Pega 2 día, 3=Pega 3 día, 4=Pega 4 día, 5=Pega 2 noche, 6=Pega 3 noche,
7=Pega 4 noche, 8=Loto, 9=Revancha.

next_draw_date/next_session for Pega 2/3/4 — confirmed the raw ElectronicDrawing API response
has NO next-draw-shaped field at all (no nextDrawDate/nextGameSeqNum like Arizona's own API has),
so unlike az_pick3_scraper.py this can't just be decoded off the response; it's computed here from
wall-clock time instead. Schedule re-confirmed directly against loteriasdepuertorico.pr.gov's own
"Pega 2, 3, 4" page (https://loteriasdepuertorico.pr.gov/loteria-electronica/pega-2-3-4/, raw HTML
fetched 2026-11-10): "Para jugarle a los Pega tienes 2 oportunidades diarias de lunes a sábado con
sorteos diurnos a las 2pm y nocturnos a las 10pm." — Monday-Saturday ONLY (no Sunday draw for any
of the 3 games), day session 2:00 PM AST, night session 10:00 PM AST (Puerto Rico observes no DST,
so AST == America/Puerto_Rico year-round). compute_next_pega_draw() below walks forward from "now"
in that zone, skipping Sunday entirely, to find the next day/night session.
"""
import dataclasses
import datetime as dt
import json
import logging
import time
import zoneinfo
from pathlib import Path
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("pr_scraper")

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
REQUEST_TIMEOUT_SEC = 15
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 5
API_URL = "https://wordpresswebapi.azurewebsites.net/api/ElectronicDrawing"
OUTPUT_DIR = Path("data") / "puerto-rico"
MAX_HISTORY = 100

PR_TZ = zoneinfo.ZoneInfo("America/Puerto_Rico")  # AST year-round, no DST
PEGA_SESSION_HOUR = {"day": 14, "night": 22}  # 2:00 PM / 10:00 PM AST


LOTO_WEEKDAYS = {0, 2, 4}  # Monday, Wednesday, Friday


def compute_next_loto_draw(after_date_iso: str) -> str:
    """Next Loto Cash/Revancha session strictly after the most recently scraped draw's own date
    (not "now" — see module doc for why: exact clock time is unconfirmed, so stepping from the
    latest real draw avoids needing it, same pattern as sc_scraper.py's/tn_scraper.py's own
    `_next_draw_date`)."""
    d = dt.date.fromisoformat(after_date_iso)
    for offset in range(1, 8):
        candidate = d + dt.timedelta(days=offset)
        if candidate.weekday() in LOTO_WEEKDAYS:
            return candidate.isoformat()
    raise AssertionError("unreachable: a valid draw day occurs at least once within a week")


def compute_next_pega_draw(now: dt.datetime) -> tuple[str, str]:
    """Next Pega 2/3/4 day/night session at-or-after `now` (any tz-aware datetime),
    honoring the Monday-Saturday-only schedule (no Sunday draws). Returns
    (next_draw_date "YYYY-MM-DD", next_session "day"|"night")."""
    candidate = now.astimezone(PR_TZ)
    while True:
        if candidate.weekday() == 6:  # Sunday: no draws at all, skip to Monday 00:00
            next_day = candidate.date() + dt.timedelta(days=1)
            candidate = dt.datetime.combine(next_day, dt.time(0, 0), tzinfo=PR_TZ)
            continue
        for session in ("day", "night"):
            session_dt = candidate.replace(
                hour=PEGA_SESSION_HOUR[session], minute=0, second=0, microsecond=0
            )
            if candidate <= session_dt:
                return candidate.date().isoformat(), session
        next_day = candidate.date() + dt.timedelta(days=1)
        candidate = dt.datetime.combine(next_day, dt.time(0, 0), tzinfo=PR_TZ)


def fetch_all() -> list:
    import requests

    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(API_URL, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_SEC)
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, list):
                raise ValueError(f"响应不是数组: {type(data)}")
            return data
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("第 %d 次尝试失败: %s", attempt, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SEC * attempt)
    raise RuntimeError("重试多次后仍然失败") from last_err


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
    data = fetch_all()
    by_id = {}
    for rec in data:
        gid = rec.get("gameID")
        by_id.setdefault(gid, []).append(rec)

    now_dt = dt.datetime.now(dt.timezone.utc)
    now = now_dt.isoformat()
    next_draw_date, next_session = compute_next_pega_draw(now_dt)

    pega_map = [
        (2, "pega2", "day"), (5, "pega2", "night"),
        (3, "pega3", "day"), (6, "pega3", "night"),
        (4, "pega4", "day"), (7, "pega4", "night"),
    ]
    errors = []
    for game_id, subdir, session in pega_map:
        recs = by_id.get(game_id, [])
        if not recs:
            errors.append((subdir, session, "无数据"))
            continue
        rec = recs[0]
        draw_date = rec["drawingDate"][:10]
        results = str(rec.get("results") or rec.get("formattedResults") or "")
        digits = list(results) if results else None
        entry = {
            "draw_date": draw_date, "session": session, "digits": digits,
            "wild_ball": rec.get("wildBall"),
            "next_draw_date": next_draw_date, "next_session": next_session,
            "drawing_number": rec.get("drawingNumber"), "fetched_at": now,
            "source_url": API_URL,
        }
        save_entries(subdir, [entry], ("draw_date", "session"))

    loto = by_id.get(8, [])
    if loto:
        rec = loto[0]
        draw_date = rec["drawingDate"][:10]
        nums = [int(x) for x in (rec.get("formattedResults") or "").split("-") if x]
        entry = {
            "draw_date": draw_date, "numbers": nums[:5],
            "special_number": int(rec["formattedPlus"]) if rec.get("formattedPlus") else None,
            "multiplier": int(rec["formattedMultiplier"]) if rec.get("formattedMultiplier") else None,
            "next_jackpot_amount": rec.get("nextJackpot"), "next_draw_date": compute_next_loto_draw(draw_date),
            "drawing_number": rec.get("drawingNumber"),
            "fetched_at": now, "source_url": API_URL,
        }
        save_entries("loto-cash", [entry], ("draw_date",))
    else:
        errors.append(("loto-cash", None, "无数据"))

    revancha = by_id.get(9, [])
    if revancha:
        rec = revancha[0]
        draw_date = rec["drawingDate"][:10]
        nums = [int(x) for x in (rec.get("formattedResults") or "").split("-") if x]
        entry = {
            "draw_date": draw_date, "numbers": nums[:5],
            "special_number": int(rec["formattedPlus"]) if rec.get("formattedPlus") else None,
            "next_jackpot_amount": rec.get("nextJackpot"), "next_draw_date": compute_next_loto_draw(draw_date),
            "drawing_number": rec.get("drawingNumber"),
            "fetched_at": now, "source_url": API_URL,
        }
        save_entries("revancha", [entry], ("draw_date",))
    else:
        errors.append(("revancha", None, "无数据"))

    if errors:
        raise RuntimeError(f"{len(errors)} 个游戏抓取失败: {errors}")


if __name__ == "__main__":
    main()
