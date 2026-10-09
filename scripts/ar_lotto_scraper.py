"""
Arkansas LOTTO 抓取 — lotteryusa.com

⚠️ 数据来源合规说明：官方站 myarkansaslottery.com 整站挂着 Cloudflare 的
managed challenge（连 robots.txt 都过不去），自动化访问基本走不通；官方的
"The Club"会员站（theclub.aslplayerservices.com）虽然没有 Cloudflare、页面
也干净，但它自己的 Terms of Use 里明确写了"自动化访问本站可能构成 Computer
Fraud and Abuse Act 项下的刑事责任"，不能碰。

退而求其次用 lotteryusa.com——这是个运营多年的第三方彩票数据聚合站，没有
Cloudflare 防护，页面结构干净。它的 Terms of Use 里确实有一条"禁止
robot/spider自动访问"的条款，跟 powerball_scraper.py 当年查 Powerball.com
条款时的情况不一样（那边没有这条）。这里是在明知有这条款的情况下，经用户
确认按较低频率（一天最多十几次，只在官方开奖时间前后抓）抓取事实性开奖数据
继续做的，不是没查过条款——如果 lotteryusa.com 以后明确来信要求停止，应立
即停止这个脚本，不要跟它打擦边球。

LOTTO 规则：7个号码从1-40抽出（不是两个独立号池）——前6个是主号码，第7个
（同样在1-40范围内）是Bonus号，页面用一个单独的<li class="c-result__bonus">
标签把它和前6个主号码的<li class="c-ball">分开渲染，解析时按这个区分。

页面地址 https://www.lotteryusa.com/arkansas/lotto/ 是服务端直接渲染的静态
HTML（用 requests 就够，不需要无头浏览器），每次請求会带最近约10期历史。
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
log = logging.getLogger("ar_lotto_scraper")

def iso_date(raw: str) -> str:
    """"Oct 7, 2026" -> "2026-10-07" — lotteryusa.com's own date text, normalized to the same
    ISO yyyy-MM-dd shape every other scraper in this repo already emits, so the App's existing
    `parseLocalDate`/`DateRule` machinery (and `LiveMappingRule`'s own "yyyy-MM-dd" dateFormat)
    can parse this without a bespoke format just for these four scrapers."""
    import datetime as _dt
    return _dt.datetime.strptime(raw, "%b %d, %Y").strftime("%Y-%m-%d")

URL = "https://www.lotteryusa.com/arkansas/lotto/"
USER_AGENT = "LottoLiveApp/0.1 (+mailto:YOUR-CONTACT-EMAIL@example.com)"
REQUEST_TIMEOUT_SEC = 15
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 5

OUTPUT_DIR = Path("data")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

MONEY_PATTERN = r"\$?([\d,.]+\s*(?:Million|Billion|million|billion)?)"


@dataclasses.dataclass
class LottoResult:
    game: str
    draw_date: str  # "Oct 7, 2026" 原样保留(跟 Powerball 系列一致,由 App 端负责解析)
    numbers: list[int]  # 6个主号码
    bonus_number: Optional[int]
    jackpot_amount: Optional[str]
    fetched_at: str
    source_url: str

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def parse_money(raw: str) -> Optional[str]:
    """"$2.74 Million" -> "2.74 Million"（跟 Powerball 系列同样的"N Million"文本格式，
    交给 App 端已有的 parsePowerballMoney 解析，不在这里转成数字）。"""
    if not raw:
        return None
    m = re.search(MONEY_PATTERN, raw)
    return m.group(1).strip() if m else None


def fetch() -> list[LottoResult]:
    import requests
    from bs4 import BeautifulSoup

    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(URL, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_SEC)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            rows = soup.select("tr.c-draw-card")
            if not rows:
                raise ValueError("页面里没找到任何 c-draw-card 开奖记录行")

            results = []
            for row in rows:
                date_el = row.select_one(".c-draw-card__draw-date-sub")
                if not date_el:
                    continue
                draw_date = iso_date(date_el.get_text(strip=True))

                balls = row.select(".c-draw-card__ball-list > li.c-ball")
                bonus_el = row.select_one(".c-result__bonus .c-ball")
                numbers = [int(b.get_text(strip=True)) for b in balls]
                bonus_number = int(bonus_el.get_text(strip=True)) if bonus_el else None

                prize_el = row.select_one(".c-draw-card__prize-value")
                jackpot_amount = parse_money(prize_el.get_text(strip=True)) if prize_el else None

                results.append(LottoResult(
                    game="Arkansas LOTTO",
                    draw_date=draw_date,
                    numbers=numbers,
                    bonus_number=bonus_number,
                    jackpot_amount=jackpot_amount,
                    fetched_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                    source_url=URL,
                ))
            return results
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("第 %d 次尝试失败: %s", attempt, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SEC * attempt)
    raise RuntimeError("重试多次后仍然失败") from last_err


def validate(result: LottoResult) -> None:
    if len(result.numbers) != 6:
        raise ValueError(f"主号码应为6个，抓到 {len(result.numbers)} 个: {result.numbers}")
    if result.bonus_number is None:
        raise ValueError("没抓到 Bonus 号码")
    if not all(1 <= n <= 40 for n in result.numbers + [result.bonus_number]):
        raise ValueError(f"号码超出 1-40 范围: {result.numbers} + bonus {result.bonus_number}")


MAX_HISTORY = 100


def save_results(results: list[LottoResult]) -> Path:
    out_dir = OUTPUT_DIR / "arkansas" / "lotto"
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

    merged = new_entries + history
    merged = merged[:MAX_HISTORY]

    out_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2))
    log.info("已保存: %s (%d 条记录，本次更新 %d 条)", out_path, len(merged), len(new_entries))
    return out_path


def main():
    results = fetch()
    for r in results[:3]:
        validate(r)
    log.info("抓取结果(最新3条): %s", [r.to_dict() for r in results[:3]])
    save_results(results)


if __name__ == "__main__":
    main()
