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
"""
import dataclasses
import datetime as dt
import json
import logging
import time
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

    now = dt.datetime.now(dt.timezone.utc).isoformat()

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
            "next_jackpot_amount": rec.get("nextJackpot"), "drawing_number": rec.get("drawingNumber"),
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
            "next_jackpot_amount": rec.get("nextJackpot"), "drawing_number": rec.get("drawingNumber"),
            "fetched_at": now, "source_url": API_URL,
        }
        save_entries("revancha", [entry], ("draw_date",))
    else:
        errors.append(("revancha", None, "无数据"))

    if errors:
        raise RuntimeError(f"{len(errors)} 个游戏抓取失败: {errors}")


if __name__ == "__main__":
    main()
