"""
Lotto America（powerball.com/lotto-america）抓取

5 个红球（1-52）+ 1 个 Star Ball（1-10），外加可选的 "All Star Bonus"
倍投倍数（2x-5x，玩法类似 Powerball 的 Power Play，但不滚动头奖的那部分
不受倍投影响，跟 Power Play 的规则一致所以沿用同样的"数字紧跟标签"解析
方式）。头奖奖金结构跟 Powerball 主玩法一样，有 Estimated Jackpot +
Cash Value 两个字段。

抓取方式(2026-11-06 改为 Playwright,原因同 powerball_scraper.py):同一个
powerball.com 域名,requests 会遇到间歇性反爬拦截,改用真实 Chromium 内核
绕过。`parse_winners_section` 的 tier key 是写死的 "Jackpot"/"Match 5 +
All Star Bonus"/"Match 5" 字面量,不受 Playwright innerText 可能的全大写
渲染影响(正则本身也已带 re.IGNORECASE)。

结算值补抓(2026-11-06 新增,见 fetch_settled_jackpot()):跟 powerball_scraper.py
的 Powerball 一样,Lotto America 头奖也会滚存,首页 Next Drawing 只有
"下一期"的滚动预估,不是"这一期"的最终结算值。原来一直靠"上一次抓到的
预估"顶替,现在开奖后额外抓一次 draw-result 结算页拿真正定死的金额,抓不到
就留空,由 App 端退回原来的预估兜底逻辑。
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
log = logging.getLogger("lotto_america_scraper")

URL = "https://www.powerball.com/lotto-america"
DRAW_RESULT_URL_TEMPLATE = "https://www.powerball.com/draw-result?gc=lotto-america&date={date}"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
REQUEST_TIMEOUT_SEC = 15
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 5
MAX_HISTORY = 100

OUTPUT_DIR = Path("data")

DATE_PATTERN = r"[A-Za-z]{3},\s*[A-Za-z]{3}\s+\d{1,2},\s*\d{4}"
MONEY_PATTERN = r"\$?([\d,]+(?:\.\d+)?\s*(?:Million|Billion|million|billion))"
STATES_PATTERN = r"(None|[A-Z]{2}(?:,\s*[A-Z]{2})*)"


@dataclasses.dataclass
class LottoAmericaResult:
    draw_date: Optional[str]
    main_balls: list[int]
    star_ball: Optional[int]
    all_star_bonus: Optional[int]
    next_drawing: dict  # {next_draw_date, estimated_jackpot, cash_value}
    winners_by_tier: dict
    fetched_at: str
    source_url: str
    settled_jackpot: Optional[str] = None
    settled_cash_value: Optional[str] = None

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


def parse_winning_numbers(text: str) -> tuple[Optional[str], list[int], Optional[int], Optional[int]]:
    pattern = re.compile(
        r"Winning Numbers\s*\n\s*"
        rf"({DATE_PATTERN})\s*\n\s*"
        r"(\d{1,2})\s*\n\s*"
        r"(\d{1,2})\s*\n\s*"
        r"(\d{1,2})\s*\n\s*"
        r"(\d{1,2})\s*\n\s*"
        r"(\d{1,2})\s*\n\s*"
        r"(\d{1,2})"
        r"(?:\s*\n\s*All Star Bonus\s*\n\s*(\d+)x)?",
        re.IGNORECASE,
    )
    m = pattern.search(text)
    if not m:
        return None, [], None, None
    draw_date = m.group(1)
    main_balls = [int(m.group(i)) for i in range(2, 7)]
    star_ball = int(m.group(7))
    all_star_bonus = int(m.group(8)) if m.group(8) else None
    return draw_date, main_balls, star_ball, all_star_bonus


def parse_next_drawing(text: str) -> dict:
    result: dict = {}
    date_m = re.search(rf"Next Drawing\s*\n\s*({DATE_PATTERN})", text, re.IGNORECASE)
    result["next_draw_date"] = date_m.group(1) if date_m else None

    jackpot_m = re.search(rf"Estimated Jackpot\s*\n\s*{MONEY_PATTERN}", text, re.IGNORECASE)
    result["estimated_jackpot"] = jackpot_m.group(1).strip() if jackpot_m else None

    cash_m = re.search(rf"Cash Value\s*\n\s*{MONEY_PATTERN}", text, re.IGNORECASE)
    result["cash_value"] = cash_m.group(1).strip() if cash_m else None

    return result


def parse_winners_section(text: str) -> dict:
    pattern = re.compile(
        rf"Winners\s*\n\s*{DATE_PATTERN}\s*\n"
        r"Lotto America\s*\n"
        rf"Jackpot Winners\s*\n{STATES_PATTERN}\s*\n"
        rf"Match 5 \+ All Star Bonus\s*\nMultiplier Winners\s*\n{STATES_PATTERN}\s*\n"
        rf"Match 5\s*\n(?:\$[\d,]+\s*)?Winners\s*\n{STATES_PATTERN}",
        re.IGNORECASE,
    )
    m = pattern.search(text)
    if not m:
        return {}

    def states_list(raw: str) -> list[str]:
        raw = raw.strip()
        return [] if raw == "None" else [s.strip() for s in raw.split(",")]

    return {
        "Jackpot": {"states": states_list(m.group(1))},
        "Match 5 + All Star Bonus": {"states": states_list(m.group(2))},
        "Match 5": {"states": states_list(m.group(3))},
    }


def validate(main_balls: list[int], star_ball: Optional[int]) -> None:
    if len(main_balls) != 5:
        raise ValueError(f"主号码数量不对,抓到 {len(main_balls)} 个,预期 5 个: {main_balls}")
    if len(set(main_balls)) != 5:
        raise ValueError(f"主号码有重复,号码不合法: {main_balls}")
    if not all(1 <= n <= 52 for n in main_balls):
        raise ValueError(f"主号码超出 1-52 范围: {main_balls}")
    if star_ball is None:
        raise ValueError("没抓到 Star Ball 号码")
    if not (1 <= star_ball <= 10):
        raise ValueError(f"Star Ball 超出 1-10 范围: {star_ball}")


def fetch() -> LottoAmericaResult:
    from playwright.sync_api import sync_playwright

    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch()
                try:
                    page = browser.new_page(user_agent=USER_AGENT)
                    page.goto(URL, timeout=REQUEST_TIMEOUT_SEC * 1000, wait_until="domcontentloaded")
                    page.get_by_text("Winning Numbers").first.wait_for(timeout=REQUEST_TIMEOUT_SEC * 1000)
                    raw_text = page.inner_text("body")
                finally:
                    browser.close()

            text = normalize_lines(raw_text)

            draw_date, main_balls, star_ball, all_star_bonus = parse_winning_numbers(text)
            validate(main_balls, star_ball)

            return LottoAmericaResult(
                draw_date=draw_date,
                main_balls=main_balls,
                star_ball=star_ball,
                all_star_bonus=all_star_bonus,
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
    out_path = OUTPUT_DIR / "multistate" / "lotto-america" / "latest.json"
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
    return {"settled_jackpot": entry.get("settled_jackpot"), "settled_cash_value": entry.get("settled_cash_value")}


def fetch_settled_jackpot(draw_date: str) -> dict:
    """见 powerball_scraper.py 里同名函数的说明——同一套"开奖后抓结算页拿真实
    Jackpot/Cash Value,抓不到就退回预估兜底"逻辑,只是 URL 换成 Lotto America
    自己的 gc=lotto-america。"""
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
            result = {"settled_jackpot": jackpot_m.group(1).strip()}
            cash_m = re.search(rf"CASH VALUE:\s*{MONEY_PATTERN}", text, re.IGNORECASE)
            if cash_m:
                result["settled_cash_value"] = cash_m.group(1).strip()
            return result
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("结算页第 %d 次尝试失败: %s", attempt, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SEC * attempt)
    log.warning("结算页重试 %d 次后仍然失败,退回旧的预估兜底逻辑: %s", MAX_RETRIES, last_err)
    return {}


def save_result(result: LottoAmericaResult) -> Path:
    out_dir = OUTPUT_DIR / "multistate" / "lotto-america"
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
            result.settled_cash_value = settled.get("settled_cash_value")
            log.info("结算页数据: %s", settled)

    save_result(result)


if __name__ == "__main__":
    main()
