"""Kentucky Pick 4 — official API (gameNumber=17). Same pattern as ky_pick3_scraper.py."""

import dataclasses
import datetime as dt
import json
import logging
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ky_pick4_scraper")

URL = "https://www.kylottery.com/webhandlers/WinningNumbers.xhtml"
GAME_NUMBER = "17"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
REQUEST_TIMEOUT_SEC = 15
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 5
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


def ms_to_iso_date(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc).strftime("%Y-%m-%d")


def fetch() -> list[Pick4Result]:
    import requests

    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.post(
                URL,
                headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"},
                json={"gameNumber": GAME_NUMBER, "infoRequest": "2"},
                timeout=REQUEST_TIMEOUT_SEC,
            )
            resp.raise_for_status()
            data = resp.json()
            history = data.get("DRAW_HISTORY")
            if not history:
                raise ValueError(f"响应里没有 DRAW_HISTORY: {data}")

            results = []
            for entry in history:
                draw_time = (entry.get("DRAW_TIME") or "").lower()
                if draw_time not in ("midday", "evening"):
                    continue
                values = sorted(entry["DRAW_VALUES"], key=lambda v: v["DRAW_NUMBER_POSITION"])
                digits = [v["DRAW_VALUE"] for v in values]
                if len(digits) != 4:
                    continue
                results.append(Pick4Result(
                    game="Pick 4",
                    draw_date=ms_to_iso_date(entry["DRAW_DATE"]),
                    session=draw_time,
                    digits=digits,
                    fetched_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                    source_url=URL,
                ))
            if not results:
                raise ValueError(f"解析出 0 条有效结果: {data}")
            return results
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("第 %d 次尝试失败: %s", attempt, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SEC * attempt)
    raise RuntimeError("重试多次后仍然失败") from last_err


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
    results = fetch()
    log.info("抓取结果: %s", [r.to_dict() for r in results])
    save_results(results)


if __name__ == "__main__":
    main()
