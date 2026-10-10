"""
Connecticut Play3 抓取 — lotteryusa.com

合规说明同 ar_lotto_scraper.py：ctlottery.com 自己的 Terms of Use 明确写了禁止
"spidering, 'screen scraping', 'database scraping', ... or any other automatic
means" 访问本站内容——比 Arkansas 当年查到的那条更直接（这是彩票官方自己的站点
说的，不是第三方站点），所以这次直接放弃官方站，改用 lotteryusa.com（同样已经
跟用户确认过风险、按低频率抓取事实性开奖数据继续用的第三方聚合站）。

Play3 一天两期：Day（lotteryusa.com 页面叫 "Midday"，约中午12:30 ET）+ Night
（页面叫 "Evening"，约晚上9:50 ET）——两个场次各自独立的页面
(/connecticut/midday-3/ 是Day, /connecticut/play-3/ 是Night)，这里统一用
App端其它所有digit game都用的 "midday"/"evening" 这两个session值存进JSON
（跟ScrapedDigitGameResult/fetchLiveDigitGameHistory的既有假设一致），不用
Connecticut自己叫的"Day"/"Night"这两个词——games.json里这两个Game Entry的
显示名称("Play 3 (Day)"/"Play 3 (Night)")跟这个内部session值无关。

页面上另外有个"Wild Ball"加购号（<li class="c-result__bonus">单独标出来，
在c-ball--sm的主3位之外），这是个可选side bet，这次先只把它存进latest.json
的wild_ball字段，不接入matchRule判中逻辑（跟Illinois Fireball/Indiana
Superball同样的"先存不判"克制决定）。
"""

import dataclasses
import datetime as dt
import json
import logging
import time
from pathlib import Path
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ct_play3_scraper")


def iso_date(raw: str) -> str:
    import datetime as _dt
    return _dt.datetime.strptime(raw, "%b %d, %Y").strftime("%Y-%m-%d")


URLS = {
    "midday": "https://www.lotteryusa.com/connecticut/midday-3/",
    "evening": "https://www.lotteryusa.com/connecticut/play-3/",
}
USER_AGENT = "LottoLiveApp/0.1 (+mailto:YOUR-CONTACT-EMAIL@example.com)"
REQUEST_TIMEOUT_SEC = 15
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 5
FETCH_COUNT = 10
MAX_HISTORY = 100

OUTPUT_DIR = Path("data")


@dataclasses.dataclass
class Play3Result:
    game: str
    draw_date: str
    session: str
    digits: list[int]
    wild_ball: Optional[int]
    fetched_at: str
    source_url: str

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def fetch_session(session: str) -> list[Play3Result]:
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
                wild_el = row.select_one(".c-result__bonus .c-ball")
                wild_ball = int(wild_el.get_text(strip=True)) if wild_el else None
                results.append(Play3Result(
                    game="Play 3",
                    draw_date=draw_date,
                    session=session,
                    digits=digits,
                    wild_ball=wild_ball,
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


def validate(result: Play3Result) -> None:
    if len(result.digits) != 3:
        raise ValueError(f"位数不对，抓到 {len(result.digits)} 位，预期 3 位: {result.digits}")
    if not all(0 <= d <= 9 for d in result.digits):
        raise ValueError(f"数字超出 0-9 范围: {result.digits}")


def fetch() -> list[Play3Result]:
    return fetch_session("evening") + fetch_session("midday")


def save_results(results: list[Play3Result]) -> Path:
    out_dir = OUTPUT_DIR / "connecticut" / "play3"
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
