"""Illinois Pick 4 抓取 — lotteryusa.com。合规说明/Fireball处理同 il_pick3_scraper.py。

next_draw_date/next_session：同il_pick3_scraper.py——Midday 12:40 PM CT、
Evening 9:22 PM CT，每天都开，next_draw()算法同样锚定America/Chicago。
"""

import dataclasses
import datetime as dt
import json
import logging
import time
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("il_pick4_scraper")

IL_TZ = ZoneInfo("America/Chicago")
MIDDAY_DRAW_TIME = dt.time(12, 40)
EVENING_DRAW_TIME = dt.time(21, 22)


def next_draw(now: Optional[dt.datetime] = None) -> tuple[str, str]:
    now = dt.datetime.now(IL_TZ) if now is None else now.astimezone(IL_TZ)
    today = now.date()
    midday_dt = dt.datetime.combine(today, MIDDAY_DRAW_TIME, tzinfo=IL_TZ)
    evening_dt = dt.datetime.combine(today, EVENING_DRAW_TIME, tzinfo=IL_TZ)
    if now < midday_dt:
        return today.isoformat(), "midday"
    if now < evening_dt:
        return today.isoformat(), "evening"
    return (today + dt.timedelta(days=1)).isoformat(), "midday"


def iso_date(raw: str) -> str:
    import datetime as _dt
    return _dt.datetime.strptime(raw, "%b %d, %Y").strftime("%Y-%m-%d")


URLS = {
    "midday": "https://www.lotteryusa.com/illinois/midday-4/",
    "evening": "https://www.lotteryusa.com/illinois/daily-4/",
}
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
REQUEST_TIMEOUT_SEC = 15
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 5
FETCH_COUNT = 10
MAX_HISTORY = 100

OUTPUT_DIR = Path("data")


@dataclasses.dataclass
class Pick4Result:
    game: str
    draw_date: str
    session: str
    digits: list[int]
    fireball: Optional[int]
    next_draw_date: Optional[str]
    next_session: Optional[str]
    fetched_at: str
    source_url: str

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def fetch_session(session: str, next_draw_date: str, next_session: str) -> list[Pick4Result]:
    import requests
    from bs4 import BeautifulSoup

    url = URLS[session]
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_SEC)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            rows = soup.select("tr.c-draw-card")[:FETCH_COUNT]
            if not rows:
                raise ValueError(f"[{session}] 页面里没找到任何 c-draw-card 开奖记录行")

            results = []
            for row in rows:
                date_el = row.select_one(".c-draw-card__draw-date-sub")
                if not date_el:
                    continue
                draw_date = iso_date(date_el.get_text(strip=True))
                balls = row.select(".c-draw-card__ball-list > li.c-ball")
                digits = [int(b.get_text(strip=True)) for b in balls]
                fb_el = row.select_one(".c-result__bonus .c-ball")
                fireball = int(fb_el.get_text(strip=True)) if fb_el else None
                results.append(Pick4Result(
                    game="Pick 4",
                    draw_date=draw_date,
                    session=session,
                    digits=digits,
                    fireball=fireball,
                    next_draw_date=next_draw_date,
                    next_session=next_session,
                    fetched_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                    source_url=url,
                ))
            return results
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("[%s] 第 %d 次尝试失败: %s", session, attempt, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SEC * attempt)
    raise RuntimeError(f"[{session}] 重试多次后仍然失败") from last_err


def validate(result: Pick4Result) -> None:
    if len(result.digits) != 4:
        raise ValueError(f"位数不对，抓到 {len(result.digits)} 位，预期 4 位: {result.digits}")
    if not all(0 <= d <= 9 for d in result.digits):
        raise ValueError(f"数字超出 0-9 范围: {result.digits}")


def fetch() -> list[Pick4Result]:
    next_draw_date, next_session = next_draw()
    return fetch_session("evening", next_draw_date, next_session) + fetch_session("midday", next_draw_date, next_session)


def save_results(results: list[Pick4Result]) -> Path:
    out_dir = OUTPUT_DIR / "illinois" / "pick4"
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

    new_entries = [r.to_dict() for r in results]
    new_keys = {(e["draw_date"], e["session"]) for e in new_entries}
    history = [h for h in history if (h.get("draw_date"), h.get("session")) not in new_keys]

    merged = new_entries + history
    merged = merged[:MAX_HISTORY]

    out_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2))
    log.info("已保存: %s (%d 条记录，本次更新 %d 条)", out_path, len(merged), len(new_entries))
    return out_path


def main():
    results = fetch()
    for r in results[:4]:
        validate(r)
    log.info("抓取结果(最新4条): %s", [r.to_dict() for r in results[:4]])
    save_results(results)


if __name__ == "__main__":
    main()
