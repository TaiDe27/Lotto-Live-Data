"""
Powerball.com 主页抓取 — 开奖号码 + 下期开奖信息

设计思路(和之前 fantasy5_scraper.py 不一样,这里没有猜 CSS class):
用户贴过来的主页文本里能直接看到"Winning Numbers""Power Play""Estimated
Jackpot"这些固定英文标签词,数字紧跟在标签后面。既然不知道真实的 class/id
名字,就不猜——直接在 BeautifulSoup 提取出的纯文本流上,按这些标签词做正则
匹配。这样即使真实 HTML 的 class 名字跟猜测的不一样也没关系,只要标签词和
数字的相对顺序没变,就能抓到;比硬编码 CSS 选择器更抗改版。

抓取方式(2026-11-05 改为 Playwright,之前一直是 requests):确认真实问题
——powerball.com 对 requests 这类非浏览器请求有间歇性反爬拦截,同一个
URL、同一套完整浏览器请求头,成功率只有 50%-75%(实测对照:4次里混着
拿到一个固定 8964 字节的"拦截页",不是连接被拒绝,是正常 200 但内容是
假的)。换成 Playwright 真实跑 Chromium 内核(能过 JS 挑战、有真实浏览器
指纹),同等条件下连续测试 5/5 全部拿到真实页面,一次没被拦。所以这份
脚本用 Playwright 的 `page.inner_text("body")` 代替 requests+
BeautifulSoup 去拿纯文本——后面的标签词正则匹配逻辑完全不变,只是文本
来源从"HTML源码经BeautifulSoup提取"换成"浏览器渲染后直接读可见文本"，
两者格式基本一致(都是每行一个标签/数字,靠`\n`分隔)。
**仅限这一个脚本**：同样抓 powerball.com 的 Double Play 脚本
(powerball_double_play_scraper.py)还在用 requests，本次改动故意不碰。

结算值补抓(2026-11-06 新增,见 fetch_settled_jackpot()):首页的 Next Drawing
只有"下一期"滚动预估,不是"这一期"的最终结算值——之前 App 端一直拿"上一次
抓到的、开奖前的预估"顶替,平时凑巧接近但不是真值,抓取一旦断档(比如反爬
拦截那次)就会跟官网结算页对不上。现在开奖后额外抓一次 draw-result 结算页
(真正定死的 Estimated Jackpot/Cash Value),抓不到就把这两个字段留空,由
App 端退回原来的预估兜底逻辑,不影响原有行为。

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
# 结算页(2026-11-06 新增,见 fetch_settled_jackpot()的说明)的 gc= 参数,实测确认过
# (page.goto 后 innerText 里能看到真实数据,不是 Powerball 的默认兜底页)。
DRAW_RESULT_URL_TEMPLATE = "https://www.powerball.com/draw-result?gc=powerball&date={date}"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
REQUEST_TIMEOUT_SEC = 15
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 5

OUTPUT_DIR = Path("data")
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
    # 开奖后结算的真实 Jackpot/Cash Value(见 fetch_settled_jackpot())——抓不到时留 None,
    # 不是这次开奖没有头奖,只是结算页暂时没抓到;App 端会退回用上一条记录的
    # next_drawing 预估值兜底,行为跟加这两个字段之前完全一样。
    settled_jackpot: Optional[str] = None
    settled_cash_value: Optional[str] = None

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def normalize_text(html: str) -> str:
    """BeautifulSoup 取纯文本流,压缩多余空白/空行,方便后面用宽松的正则匹配。

    这份脚本自己的 fetch() 已经改用 Playwright(见文件头注释),不再调用这个
    函数——但 powerball_double_play_scraper.py 还在 import 这个函数,用在它
    自己的 requests+BeautifulSoup 抓取路径上,所以保留不动,不能删。
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n")
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    return "\n".join(lines)


def normalize_lines(raw_text: str) -> str:
    """`normalize_text()` 的 Playwright 版本:浏览器的 `innerText` 已经是渲染后的
    纯可见文本,没有标签要剥离,只需要做同样的"每行去空白、丢空行"清理,好让
    后面的正则匹配到的文本格式跟 `normalize_text()` 保持一致。
    """
    lines = [ln.strip() for ln in raw_text.split("\n") if ln.strip()]
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

    确认真实bug(2026-11-05,真实头奖被中的一期验证到的):头奖那一档
    官网原文是全大写 "Powerball JACKPOT WINNERS NJ",其他档位是
    "Match 5 $1 Million Winners None" ——"Winners"大小写不一致,但这
    条正则原来没加 re.IGNORECASE(这个文件其他正则都加了),导致全大写
    的 "WINNERS" 永远匹配不上字面量 "Winners",头奖这一档每次都被
    静默丢掉——不是偶发,是头奖只要被中,一定复现。加 re.IGNORECASE 后,
    连带着 "None" 哨兵值的判断也要跟着做大小写不敏感(呼应着改,否则
    理论上官网把 "None" 印成小写时,`states_raw == "None"` 会判断失败)。

    第二个确认真实bug(2026-11-05,换成 Playwright 之后用真实 innerText
    再验证这个正则时发现的):Playwright 读到的可见文本里,"Powerball"
    和 "JACKPOT" 是分两行渲染的("POWERBALL\nJACKPOT WINNERS\nNJ"),
    不是同一行用空格隔开——原来的 "Powerball JACKPOT" 字面量只认得中间
    是一个空格,换行会匹配不上。改成 "Powerball\s+JACKPOT" 兼容换行。

    第三个确认真实bug(同一轮换 Playwright 验证时发现的,比前两个更严重):
    Playwright 的 innerText 读到的是浏览器"渲染后的可见文本"——如果页面
    CSS 对这块区域做了 text-transform:uppercase 之类的视觉样式,innerText
    拿到的就是全大写的 "MATCH 5 + POWER PLAY",跟 BeautifulSoup 直接读
    HTML 源码拿到的 "Match 5 + Power Play" 大小写不一样,而且这次连里面
    的换行位置也可能变(实测"Powerball"和"JACKPOT"两个词之间插了个换行)。
    这件事本身不是 bug(两种工具对同一个页面本来就会读出不同的空白/大小写
    呈现),但**这个函数返回的 dict 的 key,会被 App 那边当成精确字符串比较**
    (`LottoTracker/Services/BundledDataSource.swift` 里有
    `tierName == "Powerball JACKPOT"`,还有别处按 `"Match \(mainCount)"`
    这种 Title Case 精确匹配/判断前缀)——如果这里原样把抓到的大小写/换行
    传出去,App 端的字符串比较会直接失配,头奖和"Match N"近似中奖这些数据
    会在 App 里显示不出来,等于把这次好不容易修好的 bug 换了个地方重新
    引入一次。所以这里不直接用正则捕获到的原始文本当 key,而是**自己拼出
    固定大小写、固定格式的 key**("Powerball JACKPOT" / "Match {n}" /
    "Match {n} + Power Play"),跟抓取时的大小写/换行完全脱钩,保证不管
    innerText 实际渲染成什么样,输出给 App 的 key 格式永远跟 App 代码里
    写死的字符串一致。
    """
    winners = {}
    for m in re.finditer(
        r"(Powerball\s+JACKPOT|Match \d(?:\s*\+\s*Power Play)?)\s*"
        rf"(?:{MONEY_PATTERN}\s*)?"
        # "None" 必须排在 [A-Z]{2} 州代码这个分支前面,不然贪婪匹配会把
        # "None" 的开头字母 N 当成一个(不存在的)单字母州代码提前截断匹配
        r"Winners\s+(None|[A-Z]{2}(?:,\s*[A-Z]{2})*)",
        text,
        re.IGNORECASE,
    ):
        raw_tier, prize, states_raw = m.group(1), m.group(2), m.group(3)
        normalized = re.sub(r"\s+", " ", raw_tier).strip()
        if normalized.upper().startswith("POWERBALL"):
            tier = "Powerball JACKPOT"
        else:
            digit_m = re.search(r"\d", normalized)
            has_power_play = "power play" in normalized.lower()
            tier = (f"Match {digit_m.group()}" + (" + Power Play" if has_power_play else "")
                    if digit_m else normalized)
        states_raw = states_raw.strip()
        states = [] if states_raw.upper() == "NONE" else [s.strip() for s in states_raw.split(",")]
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
    from playwright.sync_api import sync_playwright

    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch()
                try:
                    page = browser.new_page(user_agent=USER_AGENT)
                    page.goto(URL, timeout=REQUEST_TIMEOUT_SEC * 1000, wait_until="domcontentloaded")
                    # 真实浏览器加载完 HTML 之后,这个页面还要等客户端 JS 把
                    # 号码渲染出来——实测过(2026-11-05)刚 navigate 完 innerText
                    # 还很短(没渲染完)。等"Winning Numbers"这个真实文字出现,
                    # 比写死 sleep 几秒更稳:渲染慢的时候不会等不够,渲染快的
                    # 时候也不会白等。
                    page.get_by_text("Winning Numbers").first.wait_for(timeout=REQUEST_TIMEOUT_SEC * 1000)
                    raw_text = page.inner_text("body")
                finally:
                    browser.close()

            text = normalize_lines(raw_text)

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


def draw_date_to_iso(draw_date: str) -> Optional[str]:
    """"Wed, Nov 4, 2026" -> "2026-11-04",给结算页的 URL 用。"""
    try:
        return dt.datetime.strptime(draw_date, "%a, %b %d, %Y").strftime("%Y-%m-%d")
    except ValueError:
        return None


def existing_settled_jackpot(draw_date: str) -> dict:
    """开奖之后,这个值就已经定死不会再变了——如果 latest.json 里这期记录已经有
    settled_jackpot,直接复用,不用每次抓取周期都重新打开一次结算页。"""
    out_path = OUTPUT_DIR / "multistate" / "powerball" / "latest.json"
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
    """开奖后,从 draw-result 结算页(不是首页)抓这一期真正定死的 Estimated
    Jackpot/Cash Value——首页的 Next Drawing 区块只有"下一期"的滚动预估,不是
    "这一期"的结算值(2026-11-06 跟用户确认过的真实问题:之前一直拿"上一次
    抓到的、开奖前的预估"顶替"这期的结算值",平时凑巧接近,但只要抓取中间
    断过档(比如这次 Playwright 迁移之前的反爬拦截),预估值就会停留在开奖前
    的某个中间值,跟官网结算页上真正的最终金额对不上)。

    最多重试 MAX_RETRIES 次;抓不到就返回空 dict,不抛异常——这是锦上添花的
    数据,抓不到不能让整个 main() 失败,调用方会自动退回用旧的"上一条记录的
    next_drawing 预估"兜底,行为跟没有这个函数之前一样。
    """
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


MAX_HISTORY = 100


def save_result(result: PowerballResult) -> Path:
    """只维护一份 latest.json,内容是最近开奖记录组成的数组(最新的在最前
    面),最多保留 MAX_HISTORY 条——不再按日期单独存一份文件。

    按 draw_date 去重:同一期开奖(比如 41 分钟和 51 分钟那两次检查都抓到
    了同一期结果)只占数组里的一条,后一次抓到的数据覆盖前一次,而不是
    往数组里塞两条一模一样的记录,数组里的 100 条才真正对应 100 期不同
    的开奖,不是 100 次抓取。
    """
    out_dir = OUTPUT_DIR / "multistate" / "powerball"
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
