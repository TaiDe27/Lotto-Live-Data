"""
Powerball.com 主页抓取 — 开奖号码 + 下期开奖信息

设计思路(和之前 fantasy5_scraper.py 不一样,这里没有猜 CSS class):
用户贴过来的主页文本里能直接看到"Winning Numbers""Power Play""Estimated
Jackpot"这些固定英文标签词,数字紧跟在标签后面。既然不知道真实的 class/id
名字,就不猜——直接在 BeautifulSoup 提取出的纯文本流上,按这些标签词做正则
匹配。这样即使真实 HTML 的 class 名字跟猜测的不一样也没关系,只要标签词和
数字的相对顺序没变,就能抓到;比硬编码 CSS 选择器更抗改版。

前提:开奖号码在服务端渲染的 HTML 源码里(用户已确认,不是 JS 后加载的),
所以用 requests 就够,不需要 Playwright/浏览器内核。

合规提醒(上一轮已经查过 powerball.com 的 Terms & Conditions):
没有明确禁止 robot/spider 的条款,但有一条覆盖全站的知识产权条款——
"网站内容不得以任何方式复制,除非是访问和使用网站本身所必需" ——所以这份
脚本只抓"事实"字段(号码、日期、金额),不保存整页 HTML、不复制页面文案/
排版,降低触碰这条条款的风险。
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
log = logging.getLogger("powerball_scraper")

URL = "https://www.powerball.com/"
USER_AGENT = "LottoLiveApp/0.1 (+mailto:YOUR-CONTACT-EMAIL@example.com)"
REQUEST_TIMEOUT_SEC = 15
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 5

OUTPUT_DIR = Path("/mnt/user-data/outputs")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

DATE_PATTERN = r"[A-Za-z]{3},\s*[A-Za-z]{3}\s+\d{1,2},\s*\d{4}"  # 例:"Mon, Sep 28, 2026"
MONEY_PATTERN = r"\$?([\d,]+(?:\.\d+)?\s*(?:Million|Billion|million|billion))"


@dataclasses.dataclass
class PowerballResult:
    game: str
    draw_date: Optional[str]
    white_balls: list[int]
    red_ball: Optional[int]
    power_play: Optional[int]
    next_drawing: dict  # 见 parse_next_drawing():含 next_draw_date,再加 estimated_jackpot+cash_value 或 top_prize
    winners_by_tier: dict  # 见 parse_winners_section()
    fetched_at: str
    source_url: str

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def normalize_text(html: str) -> str:
    """BeautifulSoup 取纯文本流,压缩多余空白/空行,方便后面用宽松的正则匹配。"""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n")
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    return "\n".join(lines)


def parse_winning_numbers(text: str) -> tuple[Optional[str], list[int], Optional[int], Optional[int]]:
    """抓 'Winning Numbers' 标签后面的:开奖日期 + 5个白球 + 1个红球 + Power Play 倍数(可选)。

    ⚠️ 已知风险(用户在实测反馈里指出):这个正则是"位置优先"的——只认
    "紧跟在日期后面的头 6 个数字",不区分这 6 个数字到底属于 Base Game
    还是 Double Play。Double Play 同样是 5 白+1 红的结构,数字范围完全
    合法,`validate()` 分辨不出来。如果哪天官网首页在 Winning Numbers
    区块下面直接插入了 Double Play 的号码(不管是插在 Power Play 那行
    之前还是之后),现在这版代码有两种可能:
      1. Double Play 排在 Base Game 号码之后 → 现在的正则只抓前 6 个,
         结果依然正确(不受影响)
      2. Double Play 排在 Base Game 号码之前,或者两组号码之间没有任何
         文字标签分隔 → 会把 Double Play 的号码错认成 Base Game,而且
         不会报错,因为两组数字都能通过 validate() 的范围校验

    下面 detect_ambiguous_trailing_block() 就是针对第 2 种情况加的防线:
    检查主号码匹配完之后紧跟着的文本里,是否还有一组"没有任何文字标签、
    看起来像另一注开奖号码"的 6 个数字。如果有,直接抛异常,不猜、不吞掉
    这个风险——逼着操作者去看一眼页面结构再决定怎么改,而不是让错误数据
    悄悄流进 App。
    """
    pattern = re.compile(
        r"Winning Numbers\s*\n\s*"
        rf"({DATE_PATTERN})\s*\n\s*"
        r"(\d{1,2})\s*\n\s*"
        r"(\d{1,2})\s*\n\s*"
        r"(\d{1,2})\s*\n\s*"
        r"(\d{1,2})\s*\n\s*"
        r"(\d{1,2})\s*\n\s*"
        r"(\d{1,2})"
        r"(?:\s*\n\s*Power Play\s*(\d+)x)?",
        re.IGNORECASE,
    )
    m = pattern.search(text)
    if not m:
        return None, [], None, None

    draw_date = m.group(1)
    white_balls = [int(m.group(i)) for i in range(2, 7)]
    red_ball = int(m.group(7))
    power_play = int(m.group(8)) if m.group(8) else None

    detect_ambiguous_trailing_block(text, match_end=m.end())

    return draw_date, white_balls, red_ball, power_play


def detect_ambiguous_trailing_block(text: str, match_end: int, lookahead_chars: int = 120) -> None:
    """主号码匹配结束的位置往后看一小段,如果紧跟着另一组"裸的" 6 个数字
    (前面没有任何字母标签,只用空白/换行分隔),说明页面可能新插入了
    Double Play(或其他附加玩法)的号码块,而且没有文字标签区分——这种
    情况下不能信任"取前 6 个"这个假设,必须让人工介入,不能自动往下走。
    """
    trailing = text[match_end : match_end + lookahead_chars]
    # 允许紧跟着链接文案("View Results" "Check Your Numbers" 这类),
    # 只有紧跟着"纯数字换行"结构时才判定为可疑
    ambiguous_pattern = re.compile(
        r"^\s*(?:\d{1,2}\s*\n\s*){5}\d{1,2}\b"
    )
    if ambiguous_pattern.match(trailing):
        raise ValueError(
            "检测到 Winning Numbers 区块后面紧跟着另一组没有文字标签的 6 个"
            "数字,可能是 Double Play(或其他附加玩法)的号码被插入了页面,"
            "现有的'取前 6 个数字'逻辑无法安全区分两组号码归属。已停止抓取"
            "——请手动打开 powerball.com 核对页面结构,确认这组号码是什么、"
            "前面有没有可以用来区分的文字标签,再决定怎么改正则(比如改成"
            "要求 Base Game 数字块必须紧跟在具体的另一个标签词后面,而不是"
            "只认'日期后面头 6 个')。"
        )


def parse_next_drawing(text: str) -> dict:
    """抓 'Next Drawing' 之后的日期,以及奖金信息。

    奖金字段用一个 dict 而不是固定的 (jackpot, cash_value) 两元组返回,
    是因为不同游戏的奖金结构不一样:
    - Powerball 主玩法:头奖会滚动增长,页面用 "Estimated Jackpot" +
      "Cash Value"(现金一次性领取的折算值)两个字段
    - Double Play:头奖固定 $1000万不滚动,页面用 "Top Prize" 一个字段,
      **没有** Estimated/Cash Value 这组概念(截图里能看到,但没有拿到
      真实文本验证过这段的具体换行/标签写法,按经验推测的结构,上线前
      建议用真实抓取结果核对一次)
    这样调用方可以按 `"top_prize" in result` 还是
    `"estimated_jackpot" in result` 来判断这是哪种奖金结构,不用两边
    都塞进同一个字段名里搞混。
    """
    result: dict = {}

    date_m = re.search(rf"Next Drawing\s*\n\s*({DATE_PATTERN})", text, re.IGNORECASE)
    result["next_draw_date"] = date_m.group(1) if date_m else None

    jackpot_m = re.search(rf"Estimated Jackpot\s*{MONEY_PATTERN}", text, re.IGNORECASE)
    if jackpot_m:
        result["estimated_jackpot"] = jackpot_m.group(1).strip()
        cash_m = re.search(rf"Cash Value\s*{MONEY_PATTERN}", text, re.IGNORECASE)
        result["cash_value"] = cash_m.group(1).strip() if cash_m else None
    else:
        # 未经真实文本验证的推测路径 —— 只在 "Estimated Jackpot" 没抓到时
        # 才尝试,不影响已验证过的主 Powerball 路径
        top_prize_m = re.search(rf"Top Prize\s*{MONEY_PATTERN}", text, re.IGNORECASE)
        result["top_prize"] = top_prize_m.group(1).strip() if top_prize_m else None

    return result


def parse_winners_section(text: str) -> dict:
    """抓 'Winners' 区块里各奖级的中奖州份,例:
    'Match 5 + Power Play $2 Million Winners PR'
    'Match 5 $1 Million Winners IL, NC, NY'
    解析成 {tier: {"prize": "...", "states": [...]}}。抓不到就返回空 dict,
    不影响号码本身的抓取结果——这部分是锦上添花,不是核心字段。
    """
    winners = {}
    for m in re.finditer(
        r"(Powerball JACKPOT|Match \d(?:\s*\+\s*Power Play)?)\s*"
        rf"(?:{MONEY_PATTERN}\s*)?"
        # "None" 必须排在 [A-Z]{2} 州代码这个分支前面,不然贪婪匹配会把
        # "None" 的开头字母 N 当成一个(不存在的)单字母州代码提前截断匹配
        r"Winners\s+(None|[A-Z]{2}(?:,\s*[A-Z]{2})*)",
        text,
    ):
        tier, prize, states_raw = m.group(1), m.group(2), m.group(3)
        states_raw = states_raw.strip()
        states = [] if states_raw == "None" else [s.strip() for s in states_raw.split(",")]
        winners[tier] = {"prize": prize, "states": states}
    return winners


def validate(white_balls: list[int], red_ball: Optional[int]) -> None:
    if len(white_balls) != 5:
        raise ValueError(f"白球数量不对,抓到 {len(white_balls)} 个,预期 5 个: {white_balls}")
    if len(set(white_balls)) != 5:
        raise ValueError(f"白球有重复,号码不合法: {white_balls}")
    if not all(1 <= n <= 69 for n in white_balls):
        raise ValueError(f"白球超出 1-69 范围: {white_balls}")
    if red_ball is None:
        raise ValueError("没抓到红球(Powerball)号码")
    if not (1 <= red_ball <= 26):
        raise ValueError(f"红球超出 1-26 范围: {red_ball}")


def fetch() -> PowerballResult:
    import requests

    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(URL, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_SEC)
            resp.raise_for_status()
            text = normalize_text(resp.text)

            draw_date, white_balls, red_ball, power_play = parse_winning_numbers(text)
            validate(white_balls, red_ball)

            next_drawing = parse_next_drawing(text)
            winners = parse_winners_section(text)

            return PowerballResult(
                game="Powerball",
                draw_date=draw_date,
                white_balls=white_balls,
                red_ball=red_ball,
                power_play=power_play,
                next_drawing=next_drawing,
                winners_by_tier=winners,
                fetched_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                source_url=URL,
            )
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("第 %d 次尝试失败: %s", attempt, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SEC * attempt)
    raise RuntimeError(f"重试 {MAX_RETRIES} 次后仍然失败") from last_err


def save_result(result: PowerballResult) -> Path:
    """按 游戏/日期 存 JSON,跟之前 fantasy5_scraper.py 的存储结构保持一致,
    对应 PRD 第4.1节 '州/游戏/日期' 的数据管道设计(这里游戏本身跨州通用,
    所以用 'multistate/powerball' 而不是某个具体州名)。"""
    date_str = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    out_path = OUTPUT_DIR / "multistate" / "powerball" / f"{date_str}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    log.info("已保存: %s", out_path)
    return out_path


def main():
    result = fetch()
    log.info("抓取结果: %s", result.to_dict())
    save_result(result)


if __name__ == "__main__":
    main()
