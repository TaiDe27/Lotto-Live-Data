"""
Georgia 自有彩种共享抓取逻辑 — galottery.com 官方前端自己用的JSON API

合规说明：galottery.com 的 robots.txt 对 /en-us/ 路径完全放行(确认 /robots.txt
返回404，即没有限制性规则存在)，Terms of Use 没查到禁止自动化访问的条款——
跟 Florida/Colorado(官网自己版)/Delaware 同一档，清白来源。

API地址：https://gas-v2.p1.awc.lotteryservices.net/api/v2/draw-games/draws/<esaGameName>/<id>
这是官网 lastwinningnumbers.min.js 实际发出的同一个请求(通过 Playwright 式的
静态JS包分析 + 页面内嵌 JSON 找到的，不是蒙的)：
- 鉴权：header `x-esa-api-key`，值是页面 HTML 里字面量嵌入的
  `interactive.variables.ESA_API_KEY`，不是什么需要申请的私密订阅密钥。
- URL路径最后的 <id> 不是"第几期"的直觉编号，而是这个彩种自己的一个全局递增
  序列号，同一彩种不同session(白天/晚上)共享同一个计数器，不是分别计数。
  传 `0` 会拿到"下一期"(status=OPEN，开奖时间在未来，还没开奖结果)；要拿到
  "最近一期已开奖结果"，要把这个 id 减 1。
- 金额字段 `shareAmount`：经过与官网 Prizes & Odds 页面实际$金额表格交叉验证
  （Cash3/Cash4 各自页面上有"$0.50 Play"/"$1.00 Play"两栏），确认：
    * Cash 3 / Cash 4：API 的 shareAmount 对应的是 "$0.50 Play" 那一栏(单位是
      分，即 shareAmount/100 = $0.50投注奖金)，这个App统一按$1投注展示，所以
      要再乘以2才是$1投注奖金。
    * Georgia Five：没有$0.50投注档，shareAmount 本身就是 $1投注奖金(单位分)，
      不需要再乘2——亲自对照官网 georgia-five.html 页面上明文写的"$10,000"
      验证过，shareAmount=1000000分=$10000，直接对得上，不用翻倍。
  这个不一致是真实存在的，不是疏漏——写每个彩种自己的脚本时要分别处理，不能
  假设统一乘2。

Georgia Five 本身号码是5位"数字"(0-9，可重复，不是从一个池子里挑5个不重复的
球)——官网开奖结果里出现过 "2,5,5,6,1" 这种带重复数字的例子，证实这不是
Fantasy 5 那种池子型玩法，是 positional-digit 玩法(跟 Pick3/4/5 同一类，只是
5位)。这次先只抓它的原始开奖数字存进 latest.json，5位数字的 box 倍数奖级
(多少个 way)的 tierKey 命名規則这个App目前还没有定义过(现有的只有3位/4位的
box3way/box6way/box4way/box6way/box12way/box24way)，App端怎么建模由主会话
决定，这个脚本只负责把官方真实数据存下来。
"""

import datetime as dt
import logging
import time
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ga_shared")

API_BASE = "https://gas-v2.p1.awc.lotteryservices.net/api/v2/draw-games/draws"
SOURCE_PAGE = "https://www.galottery.com/en-us/winning-numbers.html"
USER_AGENT = "LottoLiveApp/0.1 (+mailto:YOUR-CONTACT-EMAIL@example.com)"
ESA_API_KEY = "pMI6ooicQ6aXCYY54xZyyNnyzfaKRp3LG"
REQUEST_TIMEOUT_SEC = 15
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 5
MAX_HISTORY = 100


def _headers() -> dict:
    return {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Referer": SOURCE_PAGE,
        "x-esa-api-key": ESA_API_KEY,
    }


def fetch_draw(esa_game_name: str, draw_id) -> Optional[dict]:
    """GET /draws/<esaGameName>/<id>。204(无内容)时返回 None。"""
    import requests
    import urllib.parse

    url = f"{API_BASE}/{urllib.parse.quote(esa_game_name)}/{draw_id}"
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers=_headers(), timeout=REQUEST_TIMEOUT_SEC)
            if resp.status_code == 204:
                return None
            resp.raise_for_status()
            return resp.json()
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("第 %d 次尝试失败 (%s/%s): %s", attempt, esa_game_name, draw_id, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SEC * attempt)
    raise RuntimeError(f"重试多次后仍然失败: {esa_game_name}/{draw_id}") from last_err


def fetch_recent_draws(esa_game_name: str, count: int) -> list[dict]:
    """先拿 id=0(下一期，没开奖)确定当前序列号基线，再往回取 count 期已开奖的。"""
    next_draw = fetch_draw(esa_game_name, 0)
    if not next_draw or "id" not in next_draw:
        raise ValueError(f"{esa_game_name}: 拿不到下一期的基线 id")
    next_id = int(next_draw["id"])

    draws = []
    draw_id = next_id - 1
    attempts_without_result = 0
    while len(draws) < count and attempts_without_result < 5:
        d = fetch_draw(esa_game_name, draw_id)
        if d and d.get("results"):
            draws.append(d)
            attempts_without_result = 0
        else:
            attempts_without_result += 1
        draw_id -= 1
    return draws


def session_name(draw: dict) -> Optional[str]:
    name = draw.get("name")
    return name.lower() if name else None


def draw_date_iso(draw: dict) -> str:
    """drawTime 是毫秒时间戳(UTC)；官网展示用美东时间，这里按美东日历日折算。"""
    ms = draw["drawTime"]
    # 用固定 UTC-4(夏令时)/UTC-5(冬令时)的美东时间折算日历日，跟其它脚本一样
    # 用 zoneinfo 处理，避免硬编码偏移出错。
    from zoneinfo import ZoneInfo

    eastern = dt.datetime.fromtimestamp(ms / 1000, tz=ZoneInfo("America/New_York"))
    return eastern.strftime("%Y-%m-%d")


def fetched_at_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()
