"""
Iowa Pick 4 抓取 — ialottery.com 官方站直接抓

合规说明、页面结构、Payout数据缺口说明同 ia_pick3_scraper.py，这里不重复。

Evening: https://www.ialottery.com/Pages/Games-Online/Pick4Win.aspx (约10pm CT)
Midday:  https://www.ialottery.com/Pages/Games-Online/Pick4MWin.aspx (约12:20pm CT)
"""

import dataclasses
import datetime as dt
import json
import logging
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ia_pick4_scraper")


def iso_date(raw: str) -> str:
    return dt.datetime.strptime(raw, "%m/%d/%Y").strftime("%Y-%m-%d")


URLS = {
    "evening": "https://www.ialottery.com/Pages/Games-Online/Pick4Win.aspx",
    "midday": "https://www.ialottery.com/Pages/Games-Online/Pick4MWin.aspx",
}
USER_AGENT = "LottoLiveApp/0.1 (+mailto:YOUR-CONTACT-EMAIL@example.com)"
REQUEST_TIMEOUT_SEC = 15
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 5
FETCH_COUNT = 10

OUTPUT_DIR = Path("data")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


@dataclasses.dataclass
class Pick4Result:
    game: str
    draw_date: str
    session: str
    digits: list[int]
    fetched_at: str
    source_url: str

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def fetch_session(session: str) -> list[Pick4Result]:
    import requests
    from bs4 import BeautifulSoup

    url = URLS[session]
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_SEC)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            table = soup.select_one("table#color")
            if not table:
                raise ValueError(f"[{session}] 页面里没找到 table#color 开奖表格")
            rows = table.select("tr")[1:][:FETCH_COUNT]

            results = []
            for row in rows:
                cells = row.select("td")
                if len(cells) != 2:
                    continue
                draw_date = iso_date(cells[0].get_text(strip=True))
                digits = [int(d.strip()) for d in cells[1].get_text(strip=True).split("-")]
                results.append(Pick4Result(
                    game="Pick 4",
                    draw_date=draw_date,
                    session=session,
                    digits=digits,
                    fetched_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                    source_url=url,
                ))
            if not results:
                raise ValueError(f"[{session}] 解析出的记录为空")
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
    return fetch_session("evening") + fetch_session("midday")


MAX_HISTORY = 100


def save_results(results: list[Pick4Result]) -> Path:
    out_dir = OUTPUT_DIR / "iowa" / "pick-4"
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
