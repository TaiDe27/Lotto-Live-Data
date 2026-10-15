"""
Mississippi own-games — mslottery.com's own WordPress/Beaver-Builder pages render the latest
winning numbers server-side, confirmed by curling each page with a plain browser UA (no JS needed):
  - /games/cash-3/ and /games/cash-4/: `.cash3winnumbers-wrap`/`.cash4winnumbers-wrap`, each with
    two `.lotto-numbers` blocks (class "EVE"/"MIDDAY") plus a trailing `.fireball` digit.
  - /games/cash-pop/: `.cashpopwinnumbers-wrap`, two `.lotto-numbers` blocks (EVE/MIDDAY), one
    number each (`Game.isVariablePickPayout`-style membership pick, reused from Indiana's design).
  - /games/mm5/ (Mississippi Match 5 — NOT a guessable "/games/match-5/" slug, which 404s; the real
    page was found via the "mm5"/"csh3"/"csh4"/"cpop" game codes used by mslottery.com's own
    "Past Drawings" links): `.match5winnumbers-wrap`, single daily session, no Fireball.
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
log = logging.getLogger("ms_scraper")

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
REQUEST_TIMEOUT_SEC = 15
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 5
OUTPUT_DIR = Path("data") / "mississippi"
MAX_HISTORY = 100


@dataclasses.dataclass
class SessionResult:
    draw_date: str
    session: str  # "midday" | "evening"
    numbers: list[int]
    fireball: Optional[int]
    fetched_at: str
    source_url: str

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclasses.dataclass
class SingleResult:
    draw_date: str
    numbers: list[int]
    fetched_at: str
    source_url: str

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def _parse_date_mdy(text: str) -> str:
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
    if not m:
        raise ValueError(f"日期格式不对: {text!r}")
    mm, dd, yyyy = m.groups()
    return f"{yyyy}-{int(mm):02d}-{int(dd):02d}"


def fetch_session_game(url: str, wrap_class: str, has_fireball: bool) -> list[SessionResult]:
    import requests
    from bs4 import BeautifulSoup

    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_SEC)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")

            wrap = soup.select_one(f".{wrap_class}")
            if wrap is None:
                raise ValueError(f"没找到 .{wrap_class}")

            results = []
            for block in wrap.select(".lotto-numbers"):
                classes = block.get("class", [])
                session = "evening" if "EVE" in classes else ("midday" if "MIDDAY" in classes else None)
                if session is None:
                    continue
                date_el = block.select_one(".draw-date")
                if date_el is None:
                    raise ValueError("没找到 .draw-date")
                draw_date = _parse_date_mdy(date_el.get_text())

                spans = block.select("span > i")
                fireball = None
                digits = []
                for i_tag in spans:
                    if "fireball" in i_tag.get("class", []):
                        fireball = int(i_tag.get_text(strip=True))
                    else:
                        digits.append(int(i_tag.get_text(strip=True)))

                if not has_fireball:
                    fireball = None
                results.append(SessionResult(
                    draw_date=draw_date, session=session, numbers=digits, fireball=fireball,
                    fetched_at=dt.datetime.now(dt.timezone.utc).isoformat(), source_url=url,
                ))
            if not results:
                raise ValueError("解析出 0 条结果")
            return results
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("第 %d 次尝试失败 (%s): %s", attempt, url, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SEC * attempt)
    raise RuntimeError(f"重试多次后仍然失败: {url}") from last_err


def fetch_match5(url: str) -> SingleResult:
    import requests
    from bs4 import BeautifulSoup

    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_SEC)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")

            wrap = soup.select_one(".match5winnumbers-wrap")
            if wrap is None:
                raise ValueError("没找到 .match5winnumbers-wrap")
            date_el = wrap.select_one(".latestdrawdate")
            if date_el is None:
                raise ValueError("没找到 .latestdrawdate")
            draw_date = _parse_date_mdy(date_el.get_text())

            numbers_el = wrap.select_one(".lotto-numbers")
            if numbers_el is None:
                raise ValueError("没找到 .lotto-numbers")
            numbers = [int(i.get_text(strip=True)) for i in numbers_el.select("span > i")]
            if len(numbers) != 5:
                raise ValueError(f"应为5个号码，抓到 {len(numbers)} 个: {numbers}")

            return SingleResult(
                draw_date=draw_date, numbers=numbers,
                fetched_at=dt.datetime.now(dt.timezone.utc).isoformat(), source_url=url,
            )
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("第 %d 次尝试失败 (%s): %s", attempt, url, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SEC * attempt)
    raise RuntimeError(f"重试多次后仍然失败: {url}") from last_err


def save_session_results(subdir: str, results: list[SessionResult]) -> Path:
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
    new_entries = [r.to_dict() for r in results]
    new_keys = {(e["draw_date"], e["session"]) for e in new_entries}
    history = [h for h in history if (h.get("draw_date"), h.get("session")) not in new_keys]
    merged = (new_entries + history)[:MAX_HISTORY]
    out_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2))
    log.info("已保存: %s (%d 条记录)", out_path, len(merged))
    return out_path


def save_single_result(subdir: str, result: SingleResult) -> Path:
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
    new_entry = result.to_dict()
    history = [h for h in history if h.get("draw_date") != new_entry["draw_date"]]
    merged = ([new_entry] + history)[:MAX_HISTORY]
    out_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2))
    log.info("已保存: %s (%d 条记录)", out_path, len(merged))
    return out_path


def main():
    errors = []
    try:
        results = fetch_session_game("https://www.mslottery.com/games/cash-3/", "cash3winnumbers-wrap", has_fireball=True)
        log.info("cash3: %s", [r.to_dict() for r in results])
        save_session_results("cash3", results)
    except Exception as e:  # noqa: BLE001
        log.error("cash3 失败: %s", e)
        errors.append(("cash3", e))

    try:
        results = fetch_session_game("https://www.mslottery.com/games/cash-4/", "cash4winnumbers-wrap", has_fireball=True)
        log.info("cash4: %s", [r.to_dict() for r in results])
        save_session_results("cash4", results)
    except Exception as e:  # noqa: BLE001
        log.error("cash4 失败: %s", e)
        errors.append(("cash4", e))

    try:
        results = fetch_session_game("https://www.mslottery.com/games/cash-pop/", "cashpopwinnumbers-wrap", has_fireball=False)
        log.info("cashpop: %s", [r.to_dict() for r in results])
        save_session_results("cashpop", results)
    except Exception as e:  # noqa: BLE001
        log.error("cashpop 失败: %s", e)
        errors.append(("cashpop", e))

    try:
        result = fetch_match5("https://www.mslottery.com/games/mm5/")
        log.info("match5: %s", result.to_dict())
        save_single_result("match5", result)
    except Exception as e:  # noqa: BLE001
        log.error("match5 失败: %s", e)
        errors.append(("match5", e))

    if errors:
        raise RuntimeError(f"{len(errors)} 个游戏抓取失败: {errors}")


if __name__ == "__main__":
    main()
