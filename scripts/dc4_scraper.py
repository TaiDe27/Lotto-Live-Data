"""
DC 4 抓取 — dclottery.com 官方站直接抓

合规说明：dclottery.com 的 robots.txt 是标准 Drupal 默认模板，只挡 /admin/、
/user/login 等后台/系统路径，没有挡 /games/ 或 /winning-numbers/ 这类公开
开奖页；没有找到独立的 Terms of Use 页面禁止自动化访问的条款——跟 Florida/
Colorado/Delaware 同一档，纯 requests 就能拿到完整数据，不需要 Playwright
(之前的调研误判"JS锁定"，实际上整页是服务端渲染的 Drupal view，纯HTML)。

DC 4/4 一天三期：Day(1:50pm)/Evening(7:50pm)/Night(11:30pm)——跟常见的只有
Midday/Evening两期不一样，所以 session 字段用 "day"/"evening"/"night" 三档，
不能硬套 App 端 fetchLiveDigitGameHistory 现有的 midday/evening 两档假设，
这里如实抓三档，App 端如何兼容由主会话处理。DC 5 只有两期：Day/Evening。

号码从 /games/dc-N 侧边栏的 "winning_numbers" view 里拿最近若干期(这个view
本身只保留最近几天，不是100期历史)。**注意**：DC3页面用的view display是
"winning_numbers_dcgames"，日期是 <time datetime="ISO..."> 标签；但DC4/DC5
页面用的是另一个display "game_winning_numbers"，日期只有纯文字"October 9,
2026\n - 11:30pm"、没有<time>标签——两种页面结构都要处理，不能假设统一。
用 view-display-id 把结果行精确限定在这两个display之一，避免match到页面上
其他不相关的 views-row（比如winner-spotlight卡片、相关游戏列表，它们也用
"views-row"这个通用class）。
"""

import dataclasses
import datetime as dt
import json
import logging
import re
import time
from pathlib import Path
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("dc4_scraper")

URL = "https://dclottery.com/games/dc-4"
USER_AGENT = "LottoLiveApp/0.1 (+mailto:YOUR-CONTACT-EMAIL@example.com)"
REQUEST_TIMEOUT_SEC = 15
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 5
DIGIT_COUNT = 4
GAME_NAME = "DC 4"
OUT_SUBDIR = "dc4"

SESSION_TIME_MAP = [
    (re.compile(r"11:\d\dpm"), "night"),
    (re.compile(r"7:\d\dpm"), "evening"),
    (re.compile(r"1:\d\dpm"), "day"),
]

MONTHS = {m: i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"], start=1)}


@dataclasses.dataclass
class DigitResult:
    game: str
    draw_date: str
    session: str
    digits: list[int]
    fetched_at: str
    source_url: str

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def session_from_text(text: str) -> Optional[str]:
    for pattern, session in SESSION_TIME_MAP:
        if pattern.search(text):
            return session
    return None


def parse_text_date(text: str) -> Optional[str]:
    """"October 9, 2026" -> "2026-10-09"."""
    m = re.search(r"([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})", text)
    if not m:
        return None
    month_name, day, year = m.group(1), int(m.group(2)), int(m.group(3))
    month = MONTHS.get(month_name)
    if month is None:
        return None
    return f"{year:04d}-{month:02d}-{day:02d}"


def fetch() -> list[DigitResult]:
    import requests
    from bs4 import BeautifulSoup

    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(URL, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_SEC)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")

            containers = soup.select(
                ".view-winning-numbers.view-display-id-winning_numbers_dcgames, "
                ".view-winning-numbers.view-display-id-game_winning_numbers"
            )
            rows = []
            for c in containers:
                rows.extend(c.select(".views-row"))
            if not rows:
                raise ValueError("页面里没找到任何开奖行")

            results = []
            fetched_at = dt.datetime.now(dt.timezone.utc).isoformat()
            for row in rows:
                time_tag = row.select_one("time.datetime")
                date_field = row.select_one(".views-field-date, .draw-date")
                text_blob = date_field.get_text() if date_field else row.get_text()

                if time_tag is not None and time_tag.get("datetime"):
                    draw_date = time_tag["datetime"][:10]
                else:
                    draw_date = parse_text_date(text_blob)
                if draw_date is None:
                    continue

                session = session_from_text(text_blob)
                if session is None:
                    continue

                balls = row.select('[class^="ball ball_"]')
                digits = []
                for b in balls:
                    txt = b.get_text(strip=True)
                    if txt.isdigit():
                        digits.append(int(txt))
                if len(digits) != DIGIT_COUNT:
                    continue

                results.append(DigitResult(
                    game=GAME_NAME,
                    draw_date=draw_date,
                    session=session,
                    digits=digits,
                    fetched_at=fetched_at,
                    source_url=URL,
                ))

            if not results:
                raise ValueError("解析出的记录为空")
            return results
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("第 %d 次尝试失败: %s", attempt, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SEC * attempt)
    raise RuntimeError("重试多次后仍然失败") from last_err


def validate(result: DigitResult) -> None:
    if len(result.digits) != DIGIT_COUNT:
        raise ValueError(f"位数不对，抓到 {len(result.digits)} 位: {result.digits}")
    if not all(0 <= d <= 9 for d in result.digits):
        raise ValueError(f"数字超出 0-9 范围: {result.digits}")


MAX_HISTORY = 100


def save_results(results: list[DigitResult]) -> Path:
    out_dir = Path("data") / "dc" / OUT_SUBDIR
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

    session_rank = {"day": 0, "evening": 1, "night": 2}
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
