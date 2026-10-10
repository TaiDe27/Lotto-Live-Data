"""
Florida 自有彩种抓取 — floridalottery.com 官方前端自己用的JSON API

合规说明：floridalottery.com 的 robots.txt 完全开放("Disallow:"留空=全部允许)，
Terms of Use 里没有禁止爬虫/自动化访问的条款(唯一相关的是一条泛泛的"不得转载
网站内容"知识产权条款，跟 Powerball.com 当年的情况一样，这份脚本只抓事实性
开奖数据，不转载站点文案)。

API地址：https://apim-website-prod-eastus.azure-api.net/drawgamesapp/getLatestDrawGames
这不是需要密钥的私有接口——用 Playwright 实际打开 floridalottery.com 首页，
抓包确认了真实前端发出的请求只带了两个普通 header(`x-partner: web` +
`Referer: https://floridalottery.com/`)，不是 Azure APIM 订阅密钥那种真鉴权，
纯 requests 带这两个 header 就能拿到完整数据，不需要无头浏览器。

这个接口一次性返回"每个彩种最新一期"的快照(不是历史列表)，所以这份脚本
一次抓全部彩种、按 GameName 拆分别写进各自的 data/florida/<game>/latest.json，
每个文件靠自己的 merge-dedupe 逐步累积历史——跟 ca_superlotto_plus_scraper.py
"定时拍快照"的模式一样，不是"一次给一批历史"。

号码字段统一是 DrawNumbers: [{"NumberPick": N, "NumberType": "wn1".."wn6"|"fb"}]，
"wn"开头是正常顺序的主/特殊号码，"fb"是Pick游戏的Fireball附加号。
各奖级字段是 Tiers: [{"PrizeLevel": ..., "Winners": N, "PrizeAmount": "..."}]——
注意 PrizeAmount 的格式因彩种而异：LOTTO/JACKPOT TRIPLE PLAY 是"$2.75 Million"
这种文本，FANTASY 5 是"56615.61"这种纯数字字符串，解析时要分开处理，不能假设
统一格式。
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
log = logging.getLogger("fl_draw_games_scraper")

URL = "https://apim-website-prod-eastus.azure-api.net/drawgamesapp/getLatestDrawGames"
SOURCE_PAGE = "https://floridalottery.com/"
USER_AGENT = "LottoLiveApp/0.1 (+mailto:YOUR-CONTACT-EMAIL@example.com)"
REQUEST_TIMEOUT_SEC = 15
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 5

OUTPUT_DIR = Path("data")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# GameName (API) -> (output folder slug, kind)
GAME_MAP = {
    "LOTTO": ("lotto", "pool"),
    "JACKPOT TRIPLE PLAY": ("jackpot-triple-play", "pool"),
    "FANTASY 5": ("fantasy5", "pool_session"),
    "PICK 2": ("pick2", "digit_session"),
    "PICK 3": ("pick3", "digit_session"),
    "PICK 4": ("pick4", "digit_session"),
    "PICK 5": ("pick5", "digit_session"),
    "CASH POP": ("cash-pop", "single_session"),
}


def iso_date(raw: str) -> str:
    """"10/07/2026 12:00:00 AM" -> "2026-10-07"."""
    return dt.datetime.strptime(raw.split(" ")[0], "%m/%d/%Y").strftime("%Y-%m-%d")


def fetch_all() -> list[dict]:
    import requests

    headers = {
        "User-Agent": USER_AGENT,
        "x-partner": "web",
        "Referer": SOURCE_PAGE,
        "Accept": "application/json",
    }
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(URL, headers=headers, timeout=REQUEST_TIMEOUT_SEC)
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, list) or not data:
                raise ValueError("接口返回了空列表或非预期格式")
            return data
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("第 %d 次尝试失败: %s", attempt, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SEC * attempt)
    raise RuntimeError("重试多次后仍然失败") from last_err


def extract_numbers(entry: dict) -> tuple[list[int], Optional[int]]:
    """DrawNumbers -> (主号码列表按wn1..wnN顺序, fireball或None)。

    Cash Pop(单选一个数字的玩法)的 NumberType 是裸的 "wn"，没有数字后缀——
    不是 "wn1" 省略成 "wn" 这么简单的巧合，是这个玩法本来就只有一个号码，
    按位置"1"处理。"""
    wn = {}
    fb = None
    for d in entry.get("DrawNumbers", []):
        t = d.get("NumberType", "")
        if t == "fb":
            fb = d.get("NumberPick")
        elif t == "wn":
            wn[1] = d.get("NumberPick")
        elif t.startswith("wn"):
            try:
                idx = int(t[2:])
            except ValueError:
                continue
            wn[idx] = d.get("NumberPick")
    numbers = [wn[i] for i in sorted(wn)]
    return numbers, fb


def main():
    raw = fetch_all()
    fetched_at = dt.datetime.now(dt.timezone.utc).isoformat()

    by_game: dict[str, list[dict]] = {}
    for entry in raw:
        name = entry.get("GameName", "").strip()
        if name not in GAME_MAP:
            continue
        slug, kind = GAME_MAP[name]
        numbers, fireball = extract_numbers(entry)
        draw_date = iso_date(entry["DrawDate"])
        draw_type = entry.get("DrawType")  # "MIDDAY"/"EVENING" or "MOR"/"MAT"/"AFT"/"EVE"/"LAT" for Cash Pop

        is_digit_game = kind == "digit_session"
        record = {
            "game": name,
            "draw_date": draw_date,
            ("digits" if is_digit_game else "numbers"): numbers,
            "fetched_at": fetched_at,
            "source_url": SOURCE_PAGE,
        }
        if fireball is not None:
            record["fireball"] = fireball
        if draw_type:
            record["session"] = draw_type.lower()
        if entry.get("NextJackpotAmount"):
            record["next_jackpot_amount"] = entry["NextJackpotAmount"]
        if entry.get("NextJackpotDate"):
            record["next_draw_date"] = iso_date(entry["NextJackpotDate"])
        tiers = entry.get("Tiers")
        if tiers:
            record["tiers"] = tiers
            # 最高奖级(数组第一个)的奖金，顺带复制一份到顶层方便App直接取——
            # 跟 ca_superlotto_plus_scraper.py 处理calottery.com那边同样的
            # "没有独立jackpot字段"问题是同一个思路。
            record["jackpot_amount"] = tiers[0].get("PrizeAmount")

        by_game.setdefault(slug, []).append(record)

    for slug, records in by_game.items():
        save_results(slug, records)


MAX_HISTORY = 100


def save_results(slug: str, new_records: list[dict]) -> Path:
    out_dir = OUTPUT_DIR / "florida" / slug
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

    def key(e: dict):
        return (e["draw_date"], e.get("session"))

    new_keys = {key(e) for e in new_records}
    history = [h for h in history if key(h) not in new_keys]

    merged = new_records + history
    merged = merged[:MAX_HISTORY]

    out_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2))
    log.info("florida/%s: 已保存 %s (%d 条记录，本次更新 %d 条)", slug, out_path, len(merged), len(new_records))
    return out_path


if __name__ == "__main__":
    main()
