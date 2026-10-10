"""
Idaho Pick 4 抓取 — idaholottery.com 官方站直接抓

合规说明同 id_cash_scraper.py。结构跟 id_pick3_scraper.py 完全一样，只是
DIGIT_COUNT=4、每个 .numbers-row 的号码列表第5个<li>才是"Sum it up!"
附加数字(丢掉)。

奖金(固定奖金制): Exact Order(straight) $5000，Any Order 4-Way(box4way)
$1200，Any Order 6-Way(box6way) $800，Any Order 12-Way(box12way) $400，
Any Order 24-Way(box24way) $200——写进 App 端 games.json。
"""

import dataclasses
import datetime as dt
import json
import logging
import re
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("id_pick4_scraper")

URL = "https://www.idaholottery.com/games/draw/pick-4"
USER_AGENT = "LottoLiveApp/0.1 (+mailto:YOUR-CONTACT-EMAIL@example.com)"
REQUEST_TIMEOUT_SEC = 15
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 5
DIGIT_COUNT = 4

OUTPUT_DIR = Path("data")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def iso_date(raw: str) -> str:
    """"10/09/26" -> "2026-10-09"."""
    return dt.datetime.strptime(raw.strip(), "%m/%d/%y").strftime("%Y-%m-%d")


@dataclasses.dataclass
class PickResult:
    game: str
    draw_date: str
    session: str  # "midday" | "evening"
    digits: list[int]
    fetched_at: str
    source_url: str

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def fetch() -> list[PickResult]:
    import requests

    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(URL, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_SEC)
            resp.raise_for_status()
            html = resp.text

            idx = html.find('id="tab4"')
            if idx < 0:
                raise ValueError("页面里没找到 Past Drawings 历史表格(#tab4)")
            chunk = html[idx:idx + 25000]

            date_rows = re.findall(
                r'<td data-title="Date">\s*([\d/]+)\s*</td>\s*<td data-title="Winning Numbers">(.*?)</td>\s*<td data-title="Jackpot">',
                chunk, re.S,
            )
            if not date_rows:
                raise ValueError("Past Drawings 表格里没解析出任何行")

            fetched_at = dt.datetime.now(dt.timezone.utc).isoformat()
            results = []
            for date_raw, cell_html in date_rows:
                draw_date = iso_date(date_raw)
                for session_tag, session in (("day", "midday"), ("night", "evening")):
                    m = re.search(
                        rf'<span class="{session_tag}">.*?<ul class="list-drawgame list-numbers[^"]*">(.*?)</ul>',
                        cell_html, re.S,
                    )
                    if not m:
                        continue
                    nums = [int(n) for n in re.findall(r"<li>\s*(\d+)\s*</li>", m.group(1))]
                    digits = nums[:DIGIT_COUNT]  # 丢掉"Sum it up!"附加的第5个数字
                    if len(digits) != DIGIT_COUNT:
                        continue
                    results.append(PickResult(
                        game="Pick 4",
                        draw_date=draw_date,
                        session=session,
                        digits=digits,
                        fetched_at=fetched_at,
                        source_url=URL,
                    ))
            if not results:
                raise ValueError("解析出的 Pick 4 记录为空")
            return results
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("第 %d 次尝试失败: %s", attempt, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SEC * attempt)
    raise RuntimeError("重试多次后仍然失败") from last_err


def validate(result: PickResult) -> None:
    if len(result.digits) != DIGIT_COUNT:
        raise ValueError(f"位数不对，抓到 {len(result.digits)} 位: {result.digits}")
    if not all(0 <= d <= 9 for d in result.digits):
        raise ValueError(f"数字超出 0-9 范围: {result.digits}")


MAX_HISTORY = 100


def save_results(results: list[PickResult]) -> Path:
    out_dir = OUTPUT_DIR / "idaho" / "pick4"
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

    session_rank = {"midday": 0, "evening": 1}
    merged = new_entries + history
    merged.sort(key=lambda e: (e["draw_date"], session_rank.get(e.get("session"), -1)), reverse=True)
    merged = merged[:MAX_HISTORY]

    out_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2))
    log.info("已保存: %s (%d 条记录，本次更新 %d 条)", out_path, len(merged), len(new_entries))
    return out_path


def main():
    results = fetch()
    for r in results:
        validate(r)
    log.info("抓取结果: %s", [r.to_dict() for r in results])
    save_results(results)


if __name__ == "__main__":
    main()
