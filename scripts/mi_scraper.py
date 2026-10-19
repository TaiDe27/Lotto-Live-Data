"""
Michigan own-games — mixed sources.

2026-10-14 update: michiganlottery.com's real GraphQL API (`POST /api`) was reverse-engineered via
real browser network captures (the official app itself calls it) — `drawResults`/`winningNumbers`
are real fields, confirmed live. Daily 3/4/5 and Daily Keno now use this directly instead of
lotteryusa.com:
  - Daily 3/4/5: the real `winningNumbers(logicalGameIdentifier, drawDate)` query returns BOTH
    `drawNumbersMid` and `drawNumbersEve` in one call — fixes a real gap where the prior
    lotteryusa.com-based scraper only ever captured ONE session (lotteryusa.com has no
    "midday-daily-3"-style URL), silently missing Michigan's real twice-daily draws.
  - Daily Keno: the real draw is **22 numbers** (confirmed live + cross-checked against Michigan's
    own official prize chart: "Match 10 of 22" / "Match 9 of 22"), NOT 10 — the prior
    lotteryusa.com-based scraper only captured 10, meaning 12 of the real winning numbers were
    invisible to this app's matching logic the whole time it was live. This is a real correctness
    bug fix, not just a new-field addition. `DAILY_KENO` is a distinct GraphQL identifier from
    `CLUB_KENO` (a separate, much-higher-frequency game this project does not build — see this
    project's standing "≤10 draws/day" rule) — don't conflate them.
  - Cash Pop: still not found. The real API's `winningNumbers` field returns `null` for
    `logicalGameIdentifier: "CASH_POP"` (confirmed live) — its real winning-number field lives
    somewhere else in the schema, not yet located. Still deferred, not guessed.
  - Fantasy 5, Classic Lotto 47: left on lotteryusa.com (already verified working, not touched).

2026-10-15 update: Poker Lotto also moved onto the real GraphQL API. `winningNumbers.drawNumbers`
for `POKER_LOTTO` returns 5 plain integers 1-52 (e.g. `[51, 25, 33, 40, 49]`), NOT card codes. The
integer→card encoding was decoded and cross-verified against a real screenshot of the official
app's results card showing exactly K♣ K♦ 8♠ 2♣ J♣ for that same draw — see `decode_poker_card`
below for the formula. Output is written in the same "RankSuitLetter" code format the existing
Swift display logic already expects (e.g. "6C"/"2H"/"AC"), so nothing downstream needed to change.
"""
import dataclasses
import datetime as dt
import json
import logging
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("mi_scraper")

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
REQUEST_TIMEOUT_SEC = 15
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 5
FETCH_COUNT = 10
OUTPUT_DIR = Path("data") / "michigan"
GRAPHQL_URL = "https://www.michiganlottery.com/api"

RANK_ORDER = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"]
SUIT_ORDER = ["H", "D", "S", "C"]  # Hearts 1-13, Diamonds 14-26, Spades 27-39, Clubs 40-52


def decode_poker_card(n: int) -> str:
    """Michigan's real GraphQL API encodes each Poker Lotto card as an integer 1-52: 13-card suit
    blocks in Hearts/Diamonds/Spades/Clubs order, rank order 2,3,...,10,J,Q,K,A within each suit
    (2 = lowest, offset 0; A = highest, offset 12). Verified: [51,25,33,40,49] decodes to
    KC/KD/8S/2C/JC, matching a real screenshot of K♣ K♦ 8♠ 2♣ J♣ for that exact draw."""
    suit_index = (n - 1) // 13
    rank_offset = (n - 1) % 13
    return f"{RANK_ORDER[rank_offset]}{SUIT_ORDER[suit_index]}"


DAILY_QUERY = """
query dailyData($logicalGameIdentifier: String, $drawDate: String) {
  winningNumbers: winningNumbers(logicalGameIdentifier: $logicalGameIdentifier, drawDate: $drawDate) {
    resultsPending
    drawNumbersMid
    drawNumbersEve
  }
}
"""

KENO_QUERY = """
query kenoData($logicalGameIdentifier: String, $drawDate: String) {
  winningNumbers: winningNumbers(logicalGameIdentifier: $logicalGameIdentifier, drawDate: $drawDate) {
    resultsPending
    drawNumbers
  }
}
"""

POKER_QUERY = """
query pokerLottoData($logicalGameIdentifier: String, $drawDate: String) {
  winningNumbers: winningNumbers(logicalGameIdentifier: $logicalGameIdentifier, drawDate: $drawDate) {
    resultsPending
    drawNumbers
  }
}
"""


def _graphql(operation_name: str, query: str, variables: dict) -> dict:
    import requests
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.post(
                GRAPHQL_URL,
                headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"},
                json={"operationName": operation_name, "query": query, "variables": variables},
                timeout=REQUEST_TIMEOUT_SEC,
            )
            resp.raise_for_status()
            payload = resp.json()
            if payload.get("errors"):
                raise ValueError(f"GraphQL errors: {payload['errors']}")
            return payload["data"]
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("[%s] 第 %d 次尝试失败: %s", operation_name, attempt, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SEC * attempt)
    raise RuntimeError(f"[{operation_name}] 重试多次后仍然失败") from last_err


def fetch_daily_both_sessions(logical_game_identifier: str, label: str) -> list[dict]:
    """Daily 3/4/5: one real API call returns BOTH Midday and Evening in the same response —
    emits two tagged entries per real draw date, same `session`-tagged shape this project's
    `fetchLiveDigitGameHistory` already expects (no Swift changes needed)."""
    now = dt.datetime.now(dt.timezone.utc)
    draw_date_param = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    data = _graphql("dailyData", DAILY_QUERY, {"logicalGameIdentifier": logical_game_identifier, "drawDate": draw_date_param})
    wn = data.get("winningNumbers") or {}
    if wn.get("resultsPending"):
        log.warning("[%s] resultsPending=true, 跳过本次", logical_game_identifier)
        return []
    today = now.date().isoformat()
    out = []
    for session_name, digits in (("midday", wn.get("drawNumbersMid")), ("evening", wn.get("drawNumbersEve"))):
        if not digits:
            continue
        out.append({"game": label, "draw_date": today, "session": session_name, "digits": digits,
                    "fetched_at": now.isoformat(), "source_url": GRAPHQL_URL})
    return out


def fetch_keno_real() -> list[dict]:
    """Daily Keno: real draw is 22 numbers (confirmed live + against Michigan's own official prize
    chart), not 10 — the prior lotteryusa.com scraper under-captured this game's real draw."""
    now = dt.datetime.now(dt.timezone.utc)
    draw_date_param = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    data = _graphql("kenoData", KENO_QUERY, {"logicalGameIdentifier": "DAILY_KENO", "drawDate": draw_date_param})
    wn = data.get("winningNumbers") or {}
    if wn.get("resultsPending") or not wn.get("drawNumbers"):
        log.warning("[DAILY_KENO] 没有可用数据，跳过本次")
        return []
    today = now.date().isoformat()
    return [{"game": "Keno", "draw_date": today, "numbers": wn["drawNumbers"],
              "fetched_at": now.isoformat(), "source_url": GRAPHQL_URL}]


def fetch_poker_lotto_real() -> list[dict]:
    """Poker Lotto: real draw is 5 plain integers 1-52 (card codes), decoded via decode_poker_card
    into the same "RankSuitLetter" format (e.g. "6C"/"AC") the existing Swift display logic expects."""
    now = dt.datetime.now(dt.timezone.utc)
    draw_date_param = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    data = _graphql("pokerLottoData", POKER_QUERY, {"logicalGameIdentifier": "POKER_LOTTO", "drawDate": draw_date_param})
    wn = data.get("winningNumbers") or {}
    if wn.get("resultsPending") or not wn.get("drawNumbers"):
        log.warning("[POKER_LOTTO] 没有可用数据，跳过本次")
        return []
    today = now.date().isoformat()
    cards = [decode_poker_card(n) for n in wn["drawNumbers"]]
    return [{"game": "Poker Lotto", "draw_date": today, "cards": cards,
              "fetched_at": now.isoformat(), "source_url": GRAPHQL_URL}]


def iso_date(raw: str) -> str:
    return dt.datetime.strptime(raw, "%b %d, %Y").strftime("%Y-%m-%d")


def _fetch_rows(url: str) -> list:
    import requests
    from bs4 import BeautifulSoup
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_SEC)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            rows = soup.select("tr.c-draw-card")[:FETCH_COUNT]
            if not rows:
                raise ValueError(f"[{url}] 没找到任何 c-draw-card 行")
            return rows
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("[%s] 第 %d 次尝试失败: %s", url, attempt, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SEC * attempt)
    raise RuntimeError(f"[{url}] 重试多次后仍然失败") from last_err


def fetch_digit(url: str, count: int, label: str) -> list[dict]:
    out = []
    for row in _fetch_rows(url):
        date_el = row.select_one(".c-draw-card__draw-date-sub")
        if not date_el:
            continue
        draw_date = iso_date(date_el.get_text(strip=True))
        balls = row.select(".c-draw-card__ball-list > li.c-ball") or row.select("li.c-ball")
        digits = [int(b.get_text(strip=True)) for b in balls]
        if len(digits) != count:
            continue
        out.append({"game": label, "draw_date": draw_date, "digits": digits,
                    "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(), "source_url": url})
    return out


def fetch_pool(url: str, count: int, label: str) -> list[dict]:
    out = []
    for row in _fetch_rows(url):
        date_el = row.select_one(".c-draw-card__draw-date-sub")
        if not date_el:
            continue
        draw_date = iso_date(date_el.get_text(strip=True))
        balls = row.select("li.c-ball")
        numbers = [int(b.get_text(strip=True)) for b in balls[:count]]
        if len(numbers) != count:
            continue
        out.append({"game": label, "draw_date": draw_date, "numbers": numbers,
                    "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(), "source_url": url})
    return out


def save(subdir: str, new_entries: list[dict]) -> None:
    out_dir = OUTPUT_DIR / subdir
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
    new_keys = {e["draw_date"] for e in new_entries}
    history = [h for h in history if h.get("draw_date") not in new_keys]
    merged = (new_entries + history)[:100]
    out_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2))
    log.info("已保存: %s (%d 条，本次更新 %d 条)", out_path, len(merged), len(new_entries))


def main():
    # 2026-10-19: 每个游戏单独 try/except 隔离（同 va_scraper.py/wa_scraper.py 的写法）——
    # 之前是从上到下顺序执行、任何一个失败就整体中断，Daily 3 排第一个，只要它卡超时，
    # 后面 Daily4/5/Fantasy5/Classic Lotto 47/Keno/Poker Lotto 这一轮全部不会被尝试，
    # 即使它们本来能抓成功。密歇根官网在部分早晨(ET)时段偶发连接卡死到超时（已用真实失败
    # 日志+本机同请求秒回确认，不是永久性封锁），这个隔离让这种偶发问题只丢一个游戏的数据，
    # 不连带拖死其余6个。
    jobs = [
        ("daily3", lambda: save("daily3", fetch_daily_both_sessions("DAILY_3", "Daily 3"))),
        ("daily4", lambda: save("daily4", fetch_daily_both_sessions("DAILY_4", "Daily 4"))),
        ("daily5", lambda: save("daily5", fetch_daily_both_sessions("DAILY_5", "Daily 5"))),
        ("fantasy5", lambda: save("fantasy5", fetch_pool("https://www.lotteryusa.com/michigan/fantasy-5/", 5, "Fantasy 5"))),
        ("classiclotto47", lambda: save("classiclotto47", fetch_pool("https://www.lotteryusa.com/michigan/classic-lotto-47/", 6, "Classic Lotto 47"))),
        ("keno", lambda: save("keno", fetch_keno_real())),
        ("pokerlotto", lambda: save("pokerlotto", fetch_poker_lotto_real())),
    ]
    errors = []
    for name, fn in jobs:
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            log.error("%s failed: %s", name, e)
            errors.append((name, e))

    if errors:
        raise RuntimeError(f"{len(errors)} game(s) failed: {errors}")
    log.info("完成")


if __name__ == "__main__":
    main()
