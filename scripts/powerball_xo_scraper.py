"""
Powerball Xs & Os（powerball.com/xo）抓取

跟系列里其它玩法不一样：这个玩法开出来的不是号码，是 8 支 NFL 球队的缩写
（从全部 32 支队伍里抽），每周日晚 10 点 ET 开一次。沿用 powerball_scraper.py
同一套思路——不猜 CSS class，在纯文本流上按固定标签词（"Winning Teams"
"Next Drawing" "Estimated Jackpot" "Winners"）做正则匹配，标签词和数据的
相对顺序不变就能抓到，比硬编码 CSS 选择器更抗改版。

这个玩法只公布 Estimated Jackpot，没有 Cash Value（头奖奖金结构本身就跟
Powerball 主玩法不一样，不是"年金 vs 一次性"的选择）。

抓取方式(2026-11-06 改为 Playwright,原因同 powerball_scraper.py):同一个
powerball.com 域名,requests 会遇到间歇性反爬拦截,改用真实 Chromium 内核
绕过。实测验证时在 `parse_winners_section` 里发现并修复了两个真实 bug
(缺 re.IGNORECASE 导致全大写 innerText 匹配不上、原样用大写捕获文本当
tier key 会让 App 端精确字符串比较失配)——详见该函数内的注释。

结算值补抓(2026-11-06 新增,见 fetch_settled_jackpot()):这个玩法的 Jackpot
也会滚存,跟 Powerball/Lotto America 一样有"首页只有下一期预估,不是这一期
结算值"的问题。开奖后额外抓一次 draw-result 结算页拿真正定死的 Estimated
Jackpot(这个玩法没有 Cash Value,只补这一个字段),抓不到就留空退回预估
兜底。
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
DRAW_RESULT_URL_TEMPLATE = "https://www.powerball.com/draw-result?gc=powerball-xs-os&date={date}"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
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
    settled_jackpot: Optional[str] = None

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def normalize_text(html: str) -> str:
    """requests+BeautifulSoup 路径用的纯文本清理(现在 fetch() 已经不调用了,
    留着是因为跟 powerball_scraper.py 保持同样的函数名习惯,降低改动面)。"""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n")
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    return "\n".join(lines)


def normalize_lines(raw_text: str) -> str:
    """Playwright `page.inner_text()` 版本:已经是渲染后的可见文本,不用剥
    HTML 标签,只做跟 normalize_text() 一样的"每行去空白、丢空行"清理。"""
    lines = [ln.strip() for ln in raw_text.split("\n") if ln.strip()]
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
    例如 "KS (2)"，表示那个州有 2 组中奖）。

    确认真实bug(2026-11-06,换成 Playwright 后用真实 innerText 验证时发现的,
    跟 powerball_scraper.py 的 "Powerball JACKPOT"/"Match N" 是同一类问题):
    1) 这里原来的正则没加 re.IGNORECASE,而真实渲染出来的文本是全大写的
       "MATCH 8 OF 8 TEAMS"/"WINNERS",导致正则永远匹配不上,`winners_by_tier`
       每次都是空 dict——不是偶发,是必现。
    2) 就算加上 re.IGNORECASE 让正则匹配上,直接拿捕获到的原始大写文本当
       tier dict 的 key 还是不行——`LottoTracker/Services/BundledDataSource.swift`
       (`tierName == "Match 8 of 8 Teams"`)和
       `LottoTracker/Views/Results/GameCardView.swift`
       (`$0.tierKey == "Match 7 of 8 Teams"`)都是精确字符串比较,全大写的
       key 会让 App 端这两处匹配全部静默失败。所以跟主脚本一样,不直接用
       捕获到的原始文本当 key,而是从捕获到的数字重新拼出固定大小写的
       "Match N of M Teams"。
    """
    winners = {}
    for m in re.finditer(
        r"(Match\s+(\d+)\s+of\s+(\d+)\s+Teams)\s*\n"
        r"(?:\$[\d,]+\s*)?(?:Jackpot\s*)?Winners\s*\n"
        r"(None|[A-Z]{2}(?:\s*\(\d+\))?(?:,\s*[A-Z]{2}(?:\s*\(\d+\))?)*)",
        text,
        re.IGNORECASE,
    ):
        matched, total, states_raw = m.group(2), m.group(3), m.group(4).strip()
        tier = f"Match {matched} of {total} Teams"
        states = [] if states_raw.upper() == "NONE" else [s.strip() for s in states_raw.split(",")]
        winners[tier] = {"states": states}
    return winners


def validate(teams: list[str]) -> None:
    if len(teams) != 8:
        raise ValueError(f"球队数量不对,抓到 {len(teams)} 个,预期 8 个: {teams}")


def fetch() -> PowerballXOResult:
    from playwright.sync_api import sync_playwright

    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch()
                try:
                    page = browser.new_page(user_agent=USER_AGENT)
                    page.goto(URL, timeout=REQUEST_TIMEOUT_SEC * 1000, wait_until="domcontentloaded")
                    page.get_by_text("Winning Teams").first.wait_for(timeout=REQUEST_TIMEOUT_SEC * 1000)
                    raw_text = page.inner_text("body")
                finally:
                    browser.close()

            text = normalize_lines(raw_text)

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


def draw_date_to_iso(draw_date: str) -> Optional[str]:
    try:
        return dt.datetime.strptime(draw_date, "%a, %b %d, %Y").strftime("%Y-%m-%d")
    except ValueError:
        return None


def existing_settled_jackpot(draw_date: str) -> dict:
    out_path = OUTPUT_DIR / "multistate" / "powerball-xo" / "latest.json"
    if not out_path.exists():
        return {}
    try:
        history = json.loads(out_path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(history, list) or not history:
        return {}
    entry = history[0]
    if entry.get("draw_date") != draw_date or not entry.get("settled_jackpot"):
        return {}
    return {"settled_jackpot": entry.get("settled_jackpot")}


def fetch_settled_jackpot(draw_date: str) -> dict:
    """见 powerball_scraper.py 里同名函数的说明——同一套逻辑,只是 URL 换成
    这个玩法自己的 gc=powerball-xs-os,而且没有 Cash Value 要抓。"""
    from playwright.sync_api import sync_playwright

    iso_date = draw_date_to_iso(draw_date)
    if not iso_date:
        return {}
    url = DRAW_RESULT_URL_TEMPLATE.format(date=iso_date)

    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch()
                try:
                    page = browser.new_page(user_agent=USER_AGENT)
                    page.goto(url, timeout=REQUEST_TIMEOUT_SEC * 1000, wait_until="domcontentloaded")
                    page.get_by_text("ESTIMATED JACKPOT").first.wait_for(timeout=REQUEST_TIMEOUT_SEC * 1000)
                    raw_text = page.inner_text("body")
                finally:
                    browser.close()

            text = normalize_lines(raw_text)
            jackpot_m = re.search(rf"ESTIMATED JACKPOT:\s*{MONEY_PATTERN}", text, re.IGNORECASE)
            if not jackpot_m:
                return {}
            return {"settled_jackpot": jackpot_m.group(1).strip()}
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("结算页第 %d 次尝试失败: %s", attempt, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SEC * attempt)
    log.warning("结算页重试 %d 次后仍然失败,退回旧的预估兜底逻辑: %s", MAX_RETRIES, last_err)
    return {}


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

    if result.draw_date:
        cached = existing_settled_jackpot(result.draw_date)
        settled = cached if cached else fetch_settled_jackpot(result.draw_date)
        if settled:
            result.settled_jackpot = settled.get("settled_jackpot")
            log.info("结算页数据: %s", settled)

    save_result(result)


if __name__ == "__main__":
    main()
