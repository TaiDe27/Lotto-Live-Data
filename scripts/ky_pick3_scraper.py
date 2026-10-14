"""
Kentucky Pick 3 抓取 — lotteryusa.com

合规说明同本仓库其他州：kylottery.com官网真实开奖数据走的是需要会话令牌的内部API
(kys-v2.p1.awc.lotteryservices.net/api/v2/draw-games/draws，未登录返回401 NOT_AUTHORIZED)，
前端页面本身(含/apps/draw_games/pastwinning.html)是JS渲染壳，服务端HTML里没有真实号码。
改用lotteryusa.com(已有先例的第三方聚合站，低频率抓取事实性开奖数据)。

一天两期：Evening用 /kentucky/pick-3/，Midday用 /kentucky/midday-pick-3/ —— 两个独立页面，
跟 ar_cash3_scraper.py 的处理方式一样。
"""

import dataclasses
import datetime as dt
import json
import logging
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ky_pick3_scraper")


def iso_date(raw: str) -> str:
    import datetime as _dt
    return _dt.datetime.strptime(raw, "%b %d, %Y").strftime("%Y-%m-%d")


URLS = {
    "evening": "https://www.lotteryusa.com/kentucky/pick-3/",
    "midday": "https://www.lotteryusa.com/kentucky/midday-pick-3/",
}
USER_AGENT = "LottoLiveApp/0.1 (+mailto:YOUR-CONTACT-EMAIL@example.com)"
REQUEST_TIMEOUT_SEC = 15
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 5
FETCH_COUNT = 10

OUTPUT_DIR = Path("data")


@dataclasses.dataclass
class Pick3Result:
    game: str
    draw_date: str
    session: str
    digits: list[int]
    fetched_at: str
    source_url: str

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def fetch_session(session: str) -> list[Pick3Result]:
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
                raise ValueError(f"[{session}] 没找到任何 c-draw-card 行")
            results = []
            for row in rows:
                date_el = row.select_one(".c-draw-card__draw-date-sub")
                if not date_el:
                    continue
                draw_date = iso_date(date_el.get_text(strip=True))
                balls = row.select(".c-draw-card__ball-list > li.c-ball") or row.select("li.c-ball")
                digits = [int(b.get_text(strip=True)) for b in balls]
                if len(digits) != 3:
                    continue
                results.append(Pick3Result(
                    game="Pick 3", draw_date=draw_date, session=session, digits=digits,
                    fetched_at=dt.datetime.now(dt.timezone.utc).isoformat(), source_url=url,
                ))
            return results
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("[%s] 第 %d 次尝试失败: %s", session, attempt, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SEC * attempt)
    raise RuntimeError(f"[{session}] 重试多次后仍然失败") from last_err


def save_results(results: list[Pick3Result]) -> Path:
    out_dir = OUTPUT_DIR / "kentucky" / "pick3"
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
    merged = merged[:100]
    out_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2))
    log.info("已保存: %s (%d 条，本次更新 %d 条)", out_path, len(merged), len(new_entries))
    return out_path


def main():
    all_results = fetch_session("evening") + fetch_session("midday")
    log.info("抓取结果(最新): %s", [r.to_dict() for r in all_results[:4]])
    save_results(all_results)


if __name__ == "__main__":
    main()
