"""Kentucky Cash Ball 225 — lotteryusa.com (official site's real API needs auth, see ky_pick3_scraper.py)."""
import dataclasses, datetime as dt, json, logging, time
from pathlib import Path
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ky_cashball_scraper")


def iso_date(raw: str) -> str:
    return dt.datetime.strptime(raw, "%b %d, %Y").strftime("%Y-%m-%d")


URL = "https://www.lotteryusa.com/kentucky/cashball/"
USER_AGENT = "LottoLiveApp/0.1 (+mailto:YOUR-CONTACT-EMAIL@example.com)"
REQUEST_TIMEOUT_SEC = 15
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 5
FETCH_COUNT = 10
OUTPUT_DIR = Path("data")


@dataclasses.dataclass
class CashBallResult:
    game: str
    draw_date: str
    numbers: list[int]
    bonus_number: Optional[int]
    fetched_at: str
    source_url: str

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def fetch() -> list[CashBallResult]:
    import requests
    from bs4 import BeautifulSoup
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(URL, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_SEC)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            rows = soup.select("tr.c-draw-card")[:FETCH_COUNT]
            if not rows:
                raise ValueError("没找到任何 c-draw-card 行")
            results = []
            for row in rows:
                date_el = row.select_one(".c-draw-card__draw-date-sub")
                if not date_el:
                    continue
                draw_date = iso_date(date_el.get_text(strip=True))
                balls = row.select("li.c-ball")
                if len(balls) < 4:
                    continue
                numbers = [int(b.get_text(strip=True)) for b in balls[:4]]
                bonus_el = row.select_one(".c-result__bonus .c-ball")
                bonus_number = int(bonus_el.get_text(strip=True)) if bonus_el else (
                    int(balls[4].get_text(strip=True)) if len(balls) > 4 else None)
                results.append(CashBallResult(
                    game="Cash Ball 225", draw_date=draw_date, numbers=numbers, bonus_number=bonus_number,
                    fetched_at=dt.datetime.now(dt.timezone.utc).isoformat(), source_url=URL,
                ))
            return results
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("第 %d 次尝试失败: %s", attempt, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SEC * attempt)
    raise RuntimeError("重试多次后仍然失败") from last_err


def save_results(results: list[CashBallResult]) -> Path:
    out_dir = OUTPUT_DIR / "kentucky" / "cashball"
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
    new_keys = {e["draw_date"] for e in new_entries}
    history = [h for h in history if h.get("draw_date") not in new_keys]
    merged = (new_entries + history)[:100]
    out_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2))
    log.info("已保存: %s (%d 条，本次更新 %d 条)", out_path, len(merged), len(new_entries))
    return out_path


def main():
    results = fetch()
    log.info("抓取结果(最新3条): %s", [r.to_dict() for r in results[:3]])
    save_results(results)


if __name__ == "__main__":
    main()
