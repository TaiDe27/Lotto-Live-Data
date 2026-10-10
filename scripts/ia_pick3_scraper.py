"""
Iowa Pick 3 抓取 — ialottery.com 官方站直接抓

合规说明：ialottery.com 的 Terms of Use 里有一条明确禁止条款——
"you shall not ... page-scrape, robot, spider, hack, password mine or use
any similar automatic or manual program or process to use the Site."
这条已经在当前会话里明确告知用户，用户选择接受风险、按较低频率(一天最多
十几次，只在官方开奖时间前后抓)继续抓取事实性开奖数据，不是没查条款——
跟 Colorado/Connecticut 的处理方式一样。如果 ialottery.com 以后明确来信
要求停止，应立即停止这个脚本。

页面是老式 ASP.NET WebForms 服务端渲染（用 requests 就够，不需要无头浏览器），
Evening 和 Midday 是两个独立 URL：
  - https://www.ialottery.com/Pages/Games-Online/Pick3Win.aspx  = Evening（晚场，约10pm，比大多数州晚）
  - https://www.ialottery.com/Pages/Games-Online/Pick3MWin.aspx = Midday（午场，约12:20pm）
两边 Central Time。页面上是一张 Date/Numbers 两列表格，一次给最近约15期历史，
数字格式"6 - 2 - 9"，日期格式"10/9/2026"(M/D/YYYY)。

⚠️ Payout 金额：官网没有找到任何文字版的 Straight/Box 奖金表(两个独立的
"Prizes"页面只有"各玩法每期中奖注数"的历史统计表，不是奖金金额；另一个
"MWin"系列页面纯粹是开奖号码)。这个脚本不抓奖金数据(scraper只负责事实性
开奖号码，payoutTable 是 App 端 games.json 里的静态数据，不是这个脚本的
职责)——App 端如果找不到Iowa专属的奖金数字，就按全国大多数州Pick3/4的标准
固定奖金处理(Straight $500/Box3way $160/Box6way $80 @ $1，与 Arizona/Kansas
现有数据一致)，不是这个脚本本身的缺口。
"""

import dataclasses
import datetime as dt
import json
import logging
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ia_pick3_scraper")


def iso_date(raw: str) -> str:
    """"10/9/2026" -> "2026-10-09" """
    return dt.datetime.strptime(raw, "%m/%d/%Y").strftime("%Y-%m-%d")


URLS = {
    "evening": "https://www.ialottery.com/Pages/Games-Online/Pick3Win.aspx",
    "midday": "https://www.ialottery.com/Pages/Games-Online/Pick3MWin.aspx",
}
USER_AGENT = "LottoLiveApp/0.1 (+mailto:YOUR-CONTACT-EMAIL@example.com)"
REQUEST_TIMEOUT_SEC = 15
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 5
FETCH_COUNT = 10

OUTPUT_DIR = Path("data")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


@dataclasses.dataclass
class Pick3Result:
    game: str
    draw_date: str
    session: str  # "midday" | "evening"
    digits: list[int]
    fetched_at: str
    source_url: str

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def fetch_session(session: str) -> list[Pick3Result]:
    import requests
    from bs4 import BeautifulSoup

    url = URLS[session]
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_SEC)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            table = soup.select_one("table#color")
            if not table:
                raise ValueError(f"[{session}] 页面里没找到 table#color 开奖表格")
            rows = table.select("tr")[1:][:FETCH_COUNT]  # 跳过表头行

            results = []
            for row in rows:
                cells = row.select("td")
                if len(cells) != 2:
                    continue
                draw_date = iso_date(cells[0].get_text(strip=True))
                digits = [int(d.strip()) for d in cells[1].get_text(strip=True).split("-")]
                results.append(Pick3Result(
                    game="Pick 3",
                    draw_date=draw_date,
                    session=session,
                    digits=digits,
                    fetched_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                    source_url=url,
                ))
            if not results:
                raise ValueError(f"[{session}] 解析出的记录为空")
            return results
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("[%s] 第 %d 次尝试失败: %s", session, attempt, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SEC * attempt)
    raise RuntimeError(f"[{session}] 重试多次后仍然失败") from last_err


def validate(result: Pick3Result) -> None:
    if len(result.digits) != 3:
        raise ValueError(f"位数不对，抓到 {len(result.digits)} 位，预期 3 位: {result.digits}")
    if not all(0 <= d <= 9 for d in result.digits):
        raise ValueError(f"数字超出 0-9 范围: {result.digits}")


def fetch() -> list[Pick3Result]:
    return fetch_session("evening") + fetch_session("midday")


MAX_HISTORY = 100


def save_results(results: list[Pick3Result]) -> Path:
    out_dir = OUTPUT_DIR / "iowa" / "pick-3"
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

    merged = new_entries + history
    merged = merged[:MAX_HISTORY]

    out_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2))
    log.info("已保存: %s (%d 条记录，本次更新 %d 条)", out_path, len(merged), len(new_entries))
    return out_path


def main():
    results = fetch()
    for r in results[:4]:
        validate(r)
    log.info("抓取结果(最新4条): %s", [r.to_dict() for r in results[:4]])
    save_results(results)


if __name__ == "__main__":
    main()
