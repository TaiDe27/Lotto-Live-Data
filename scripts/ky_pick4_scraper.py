"""Kentucky Pick 4 — lotteryusa.com. Same rationale/pattern as ky_pick3_scraper.py."""
import dataclasses, datetime as dt, json, logging, time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ky_pick4_scraper")


def iso_date(raw: str) -> str:
    return dt.datetime.strptime(raw, "%b %d, %Y").strftime("%Y-%m-%d")


URLS = {
    "evening": "https://www.lotteryusa.com/kentucky/pick-4/",
    "midday": "https://www.lotteryusa.com/kentucky/midday-pick-4/",
}
USER_AGENT = "LottoLiveApp/0.1 (+mailto:YOUR-CONTACT-EMAIL@example.com)"
REQUEST_TIMEOUT_SEC = 15
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 5
FETCH_COUNT = 10
OUTPUT_DIR = Path("data")


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
                if len(digits) != 4:
                    continue
                results.append(Pick4Result(
                    game="Pick 4", draw_date=draw_date, session=session, digits=digits,
                    fetched_at=dt.datetime.now(dt.timezone.utc).isoformat(), source_url=url,
                ))
            return results
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("[%s] 第 %d 次尝试失败: %s", session, attempt, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SEC * attempt)
    raise RuntimeError(f"[{session}] 重试多次后仍然失败") from last_err


def save_results(results: list[Pick4Result]) -> Path:
    out_dir = OUTPUT_DIR / "kentucky" / "pick4"
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
    merged = (new_entries + history)[:100]
    out_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2))
    log.info("已保存: %s (%d 条，本次更新 %d 条)", out_path, len(merged), len(new_entries))
    return out_path


def main():
    all_results = fetch_session("evening") + fetch_session("midday")
    log.info("抓取结果(最新): %s", [r.to_dict() for r in all_results[:4]])
    save_results(all_results)


if __name__ == "__main__":
    main()
