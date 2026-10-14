"""Kentucky Cash Ball 225 — official API (gameNumber=13), real draw ~11pm ET daily."""

import dataclasses
import datetime as dt
import json
import logging
import time
from pathlib import Path
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ky_cashball_scraper")

URL = "https://www.kylottery.com/webhandlers/WinningNumbers.xhtml"
GAME_NUMBER = "13"
USER_AGENT = "LottoLiveApp/0.1 (+mailto:YOUR-CONTACT-EMAIL@example.com)"
REQUEST_TIMEOUT_SEC = 15
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 5
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


def ms_to_iso_date(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc).strftime("%Y-%m-%d")


def fetch() -> list[CashBallResult]:
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
            values = data.get("DRAW_VALUES")
            if not values:
                raise ValueError(f"响应里没有 DRAW_VALUES: {data}")
            values = sorted(values, key=lambda v: v["DRAW_NUMBER_POSITION"])
            numbers = [v["DRAW_VALUE"] for v in values]
            if len(numbers) != 4:
                raise ValueError(f"主号码数量不是4个: {numbers}")
            bonus = (data.get("SPECIAL_ARGS") or {}).get("CASHBALL")
            result = CashBallResult(
                game="Cash Ball 225",
                draw_date=ms_to_iso_date(data["DRAW_DATE"]),
                numbers=numbers,
                bonus_number=bonus,
                fetched_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                source_url=URL,
            )
            return [result]
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
    log.info("抓取结果: %s", [r.to_dict() for r in results])
    save_results(results)


if __name__ == "__main__":
    main()
