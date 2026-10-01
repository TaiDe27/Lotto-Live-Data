"""
Powerball Xs & Os（powerball.com/xo）抓取

跟系列里其它玩法不一样：这个玩法开出来的不是号码，是 8 支 NFL 球队的缩写
（从全部 32 支队伍里抽），每周日晚 10 点 ET 开一次。沿用 powerball_scraper.py
同一套思路——不猜 CSS class，在纯文本流上按固定标签词（"Winning Teams"
"Next Drawing" "Estimated Jackpot" "Winners"）做正则匹配，标签词和数据的
相对顺序不变就能抓到，比硬编码 CSS 选择器更抗改版。

这个玩法只公布 Estimated Jackpot，没有 Cash Value（头奖奖金结构本身就跟
Powerball 主玩法不一样，不是"年金 vs 一次性"的选择）。
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
log = logging.getLogger("powerball_xo_scraper")

URL = "https://www.powerball.com/xo"
USER_AGENT = "LottoLiveApp/0.1 (+mailto:YOUR-CONTACT-EMAIL@example.com)"
REQUEST_TIMEOUT_SEC = 15
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 5
MAX_HISTORY = 100

OUTPUT_DIR = Path("data")

DATE_PATTERN = r"[A-Za-z]{3},\s*[A-Za-z]{3}\s+\d{1,2},\s*\d{4}"  # 例："Sun, Sep 27, 2026"
MONEY_PATTERN = r"\$?([\d,]+(?:\.\d+)?\s*(?:Million|Billion|million|billion))"
TEAM_PATTERN = r"[A-Z]{2,3}"  # NFL 队伍缩写，如 ARI/CLE/NE


@dataclasses.dataclass
class PowerballXOResult:
    draw_date: Optional[str]
    winning_teams: list[str]
    next_drawing: dict  # {next_draw_date, estimated_jackpot}
    winners_by_tier: dict  # {"Match 8 of 8 Teams": {"states": [...]}, ...}
    fetched_at: str
    source_url: str

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def normalize_text(html: str) -> str:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n")
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    return "\n".join(lines)


def parse_winning_teams(text: str) -> tuple[Optional[str], list[str]]:
    pattern = re.compile(
        r"Winning Teams\s*\n\s*"
        rf"({DATE_PATTERN})\s*\n\s*"
        rf"({TEAM_PATTERN})\s*\n\s*"
        rf"({TEAM_PATTERN})\s*\n\s*"
        rf"({TEAM_PATTERN})\s*\n\s*"
        rf"({TEAM_PATTERN})\s*\n\s*"
        rf"({TEAM_PATTERN})\s*\n\s*"
        rf"({TEAM_PATTERN})\s*\n\s*"
        rf"({TEAM_PATTERN})\s*\n\s*"
        rf"({TEAM_PATTERN})"
    )
    m = pattern.search(text)
    if not m:
        return None, []
    draw_date = m.group(1)
    teams = [m.group(i) for i in range(2, 10)]
    return draw_date, teams


def parse_next_drawing(text: str) -> dict:
    result: dict = {}
    date_m = re.search(rf"Next Drawing\s*\n\s*({DATE_PATTERN})", text, re.IGNORECASE)
    result["next_draw_date"] = date_m.group(1) if date_m else None
    jackpot_m = re.search(rf"Estimated Jackpot\s*\n\s*{MONEY_PATTERN}", text, re.IGNORECASE)
    result["estimated_jackpot"] = jackpot_m.group(1).strip() if jackpot_m else None
    return result


def parse_winners_section(text: str) -> dict:
    """'Match 8 of 8 Teams' / 'Match 7 of 8 Teams' 两档，每档后面跟一行
    "...Winners" 标签，再跟一行 None 或州份列表（可能带 "(N)" 中奖人数后缀，
    例如 "KS (2)"，表示那个州有 2 组中奖）。"""
    winners = {}
    for m in re.finditer(
        r"(Match \d+ of \d+ Teams)\s*\n"
        r"(?:\$[\d,]+\s*)?(?:Jackpot\s*)?Winners\s*\n"
        r"(None|[A-Z]{2}(?:\s*\(\d+\))?(?:,\s*[A-Z]{2}(?:\s*\(\d+\))?)*)",
        text,
    ):
        tier, states_raw = m.group(1), m.group(2).strip()
        states = [] if states_raw == "None" else [s.strip() for s in states_raw.split(",")]
        winners[tier] = {"states": states}
    return winners


def validate(teams: list[str]) -> None:
    if len(teams) != 8:
        raise ValueError(f"球队数量不对,抓到 {len(teams)} 个,预期 8 个: {teams}")


def fetch() -> PowerballXOResult:
    import requests

    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(URL, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_SEC)
            resp.raise_for_status()
            text = normalize_text(resp.text)

            draw_date, teams = parse_winning_teams(text)
            validate(teams)

            return PowerballXOResult(
                draw_date=draw_date,
                winning_teams=teams,
                next_drawing=parse_next_drawing(text),
                winners_by_tier=parse_winners_section(text),
                fetched_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                source_url=URL,
            )
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("第 %d 次尝试失败: %s", attempt, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SEC * attempt)
    raise RuntimeError(f"重试 {MAX_RETRIES} 次后仍然失败") from last_err


def save_result(result: PowerballXOResult) -> Path:
    out_dir = OUTPUT_DIR / "multistate" / "powerball-xo"
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

    new_entry = result.to_dict()
    if history and history[0].get("draw_date") == new_entry.get("draw_date"):
        history[0] = new_entry
    else:
        history.insert(0, new_entry)
    history = history[:MAX_HISTORY]

    out_path.write_text(json.dumps(history, ensure_ascii=False, indent=2))
    log.info("已保存: %s (%d 条记录)", out_path, len(history))
    return out_path


def main():
    result = fetch()
    log.info("抓取结果: %s", result.to_dict())
    save_result(result)


if __name__ == "__main__":
    main()
