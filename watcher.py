"""
軍艦島ツアー空き監視
各社の公式予約ページを定期チェックし、空きが出たらメール通知します。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import smtplib
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Callable

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
STATE_FILE = ROOT / "state.json"
LOG_FILE = ROOT / "watcher.log"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
}

STATUS_LABELS = {
    "ok": "空きあり",
    "limited": "残りわずか",
    "cancel": "満席/不可",
    "unknown": "不明",
    "error": "取得失敗",
}


def setup_logging() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
        ],
    )


@dataclass
class Settings:
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    email_from: str
    email_to: str
    target_date: str
    target_slot: str
    party_size: int
    check_interval_seconds: int
    alert_on_ok: bool
    alert_on_limited: bool

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv(ROOT / ".env")

        def req(key: str) -> str:
            value = os.getenv(key, "").strip()
            if not value:
                raise SystemExit(f".env / Secrets に {key} を設定してください")
            return value

        def flag(key: str, default: str = "true") -> bool:
            return os.getenv(key, default).strip().lower() in {"1", "true", "yes", "on"}

        return cls(
            smtp_host=req("SMTP_HOST"),
            smtp_port=int(os.getenv("SMTP_PORT", "587")),
            smtp_user=req("SMTP_USER"),
            smtp_password=req("SMTP_PASSWORD"),
            email_from=req("EMAIL_FROM"),
            email_to=req("EMAIL_TO"),
            target_date=os.getenv("TARGET_DATE", "2026-11-19").strip(),
            target_slot=os.getenv("TARGET_SLOT", "AM").strip().upper(),
            party_size=int(os.getenv("PARTY_SIZE", "2")),
            check_interval_seconds=int(os.getenv("CHECK_INTERVAL_SECONDS", "600")),
            alert_on_ok=flag("ALERT_ON_OK", "true"),
            alert_on_limited=flag("ALERT_ON_LIMITED", "true"),
        )


def response_text(response: requests.Response) -> str:
    raw = response.content
    try:
        text = raw.decode("utf-8")
        if "�" not in text[:8000]:
            return text
    except UnicodeDecodeError:
        pass
    for enc in ("cp932", "shift_jis", "euc-jp"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def http_get(url: str, session: requests.Session | None = None, **kwargs) -> str:
    sess = session or requests.Session()
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            response = sess.get(url, headers=HEADERS, timeout=40, **kwargs)
            response.raise_for_status()
            return response_text(response)
        except Exception as exc:
            last_error = exc
            logging.warning("GET失敗 (%s/%s) %s: %s", attempt, 3, url, exc)
            time.sleep(2 * attempt)
    assert last_error is not None
    raise last_error


def http_post(
    url: str,
    data: dict,
    session: requests.Session | None = None,
    params: dict | None = None,
) -> str:
    sess = session or requests.Session()
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            response = sess.post(
                url,
                headers=HEADERS,
                data=data,
                params=params,
                timeout=40,
            )
            response.raise_for_status()
            return response_text(response)
        except Exception as exc:
            last_error = exc
            logging.warning("POST失敗 (%s/%s) %s: %s", attempt, 3, url, exc)
            time.sleep(2 * attempt)
    assert last_error is not None
    raise last_error


def mark_from_symbol(token: str, party_size: int) -> str:
    token = token.strip()
    if not token:
        return "unknown"
    if any(x in token for x in ["欠航", "運休", "休"]):
        return "cancel"
    if "△" in token or "▲" in token:
        return "limited"
    if token in {"×", "x", "X", "✕", "満", "満席"} or "×" in token or "満" in token:
        return "cancel"
    if token in {"○", "◯", "〇", "o", "O"} or "○" in token or "◯" in token:
        return "ok"
    if re.fullmatch(r"\d+", token):
        seats = int(token)
        if seats >= party_size:
            return "ok"
        if seats > 0:
            return "limited"
        return "cancel"
    return "unknown"


def parse_concierge(target_date: str, party_size: int) -> dict[str, str]:
    year_month = target_date[:7]
    day = str(int(target_date[8:10]))
    url = f"https://www.gunkanjima-concierge.com/cgi/web/?c=reserve-1&YYMM={year_month}"
    html = http_get(url)
    soup = BeautifulSoup(html, "html.parser")
    target_td = None
    for td in soup.find_all("td"):
        day_el = td.find("div", class_="day")
        if day_el and day_el.get_text(strip=True) == day:
            target_td = td
            break
    if target_td is None:
        raise RuntimeError("コンシェルジュ: 対象日セルなし")

    result = {"AM": "unknown", "PM": "unknown"}
    blocks = target_td.select("div.def > div")
    for block in blocks:
        dt = block.find("dt")
        if not dt:
            continue
        label = dt.get_text(strip=True)
        slot = "AM" if "午前" in label else "PM" if "午後" in label else None
        if not slot:
            continue
        classes = block.get("class") or []
        dd = block.find("dd")
        dd_text = dd.get_text(" ", strip=True) if dd else ""
        if "disable" in classes or "満" in dd_text:
            result[slot] = "cancel"
        elif block.find("input"):
            nums = [int(n) for n in re.findall(r"\d+", dd_text)]
            if nums:
                total = sum(nums)
                if total >= party_size:
                    result[slot] = "ok"
                elif total > 0:
                    result[slot] = "limited"
                else:
                    result[slot] = "cancel"
            else:
                result[slot] = "ok"
        elif "休" in dd_text:
            result[slot] = "cancel"
        else:
            result[slot] = mark_from_symbol(dd_text, party_size)
    return result


def parse_takashima_like(
    target_date: str,
    party_size: int,
    base_url: str,
    post_data: dict,
    params: dict | None = None,
) -> dict[str, str]:
    year_month = target_date[:4] + target_date[5:7]
    day = str(int(target_date[8:10]))
    session = requests.Session()
    session.headers.update(HEADERS)
    # warm-up
    warm = base_url
    if params:
        warm = f"{base_url}?{'&'.join(f'{k}={v}' for k, v in params.items())}"
    http_get(warm, session=session)
    data = dict(post_data)
    data["yearmonth"] = year_month
    html = http_post(base_url, data=data, session=session, params=params)

    # Prefer exact cell markup.
    pattern = rf">\s*{day}\s*<p>\s*午前便\s*:\s*([^<]+)<br\s*/?>\s*午後便\s*:\s*([^<]+)\s*</p>"
    match = re.search(pattern, html)
    if not match:
        # Some pages use full-width digits or different spacing.
        pattern2 = rf">\s*{day}\s*<p>([\s\S]*?)</p>"
        match2 = re.search(pattern2, html)
        if not match2:
            raise RuntimeError(f"対象日セルなし: {base_url}")
        cell = match2.group(1)
        am_m = re.search(r"午前便\s*:\s*([^\s<]+)", cell)
        pm_m = re.search(r"午後便\s*:\s*([^\s<]+)", cell)
        if not am_m or not pm_m:
            raise RuntimeError(f"午前/午後の記号なし: {base_url}")
        am_token, pm_token = am_m.group(1), pm_m.group(1)
    else:
        am_token, pm_token = match.group(1).strip(), match.group(2).strip()

    return {
        "AM": mark_from_symbol(am_token, party_size),
        "PM": mark_from_symbol(pm_token, party_size),
    }


def parse_takashima(target_date: str, party_size: int) -> dict[str, str]:
    return parse_takashima_like(
        target_date,
        party_size,
        "https://www.gunkanjima-cruise.jp/reserve_input.php",
        post_data={},
    )


def parse_seaman(target_date: str, party_size: int) -> dict[str, str]:
    return parse_takashima_like(
        target_date,
        party_size,
        "https://www.gunkanjima-tour-reserve.jp/reserve_input.php",
        post_data={
            "course": "1",
            "language": "ja",
            "nflames": "",
            "sdatey": "",
            "sdatem": "",
            "sdated": "",
            "dweek": "",
            "bin": "",
            "bspace": "",
            "sfocus": "",
            "reset": "",
            "act": "",
        },
        params={"course": "1"},
    )


def parse_yamasa(target_date: str, party_size: int) -> dict[str, str]:
    ymd = target_date.replace("-", "")[:6] + "01"
    url = f"https://order.gunkan-jima.net/yamasa/ja/Event/Calender?ymd={ymd}&crs=10"
    html = http_get(url)
    soup = BeautifulSoup(html, "html.parser")
    day = str(int(target_date[8:10]))
    target_span = None
    for span in soup.select("span.day"):
        if span.get_text(strip=True) == day:
            target_span = span
            break
    if target_span is None:
        raise RuntimeError("やまさ海運: 対象日なし")
    state = target_span.find_next_sibling("span", class_="state")
    if state is None:
        parent = target_span.parent
        state = parent.find("span", class_="state") if parent else None
    if state is None:
        raise RuntimeError("やまさ海運: 状態なし")

    am = pm = "unknown"
    for a in state.find_all("a"):
        text = a.get_text(" ", strip=True)
        # e.g. 09:00：× / 13:00：△
        m = re.search(r"(09:00|13:00)\s*[:：]\s*(.+)", text)
        if not m:
            continue
        token = m.group(2).strip()
        # decode HTML entities like &#215; already handled by BS text
        status = mark_from_symbol(token, party_size)
        if m.group(1) == "09:00":
            am = status
        else:
            pm = status
    return {"AM": am, "PM": pm}


def parse_ebisu(target_date: str, party_size: int) -> dict[str, str]:
    """
    第七ゑびす丸(ミカタ)は日付単位。選択可ならAM/PMとも空き候補、不可なら満席扱い。
    """
    del party_size  # date-level availability only
    url = "https://mikata.in/nagasaki-tours/reservations/new?plan_id=2720"
    html = http_get(url)
    soup = BeautifulSoup(html, "html.parser")
    td = soup.find("td", class_=re.compile(rf"calendar-date-{re.escape(target_date)}"))
    if td is None:
        raise RuntimeError("第七ゑびす丸: 対象日なし")
    classes = " ".join(td.get("class") or [])
    if "calendar-select-date" in classes or "calendar-other-month-select-date" in classes:
        status = "ok"
    elif td.find("i", class_=re.compile(r"fa-minus")):
        status = "cancel"
    else:
        status = "unknown"
    return {"AM": status, "PM": status}


COMPANIES: list[dict] = [
    {
        "name": "軍艦島コンシェルジュ",
        "url": "https://www.gunkanjima-concierge.com/cgi/web/?c=reserve-1",
        "parse": parse_concierge,
    },
    {
        "name": "高島海上交通",
        "url": "https://www.gunkanjima-cruise.jp/reserve_input.php",
        "parse": parse_takashima,
    },
    {
        "name": "やまさ海運",
        "url": "https://order.gunkan-jima.net/yamasa",
        "parse": parse_yamasa,
    },
    {
        "name": "シーマン商会",
        "url": "https://www.gunkanjima-tour-reserve.jp/reserve_input.php?course=1",
        "parse": parse_seaman,
    },
    {
        "name": "第七ゑびす丸",
        "url": "https://mikata.in/nagasaki-tours/reservations/new?plan_id=2720",
        "parse": parse_ebisu,
    },
]


def fetch_availability(target_date: str, party_size: int) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    errors: list[str] = []
    for company in COMPANIES:
        name = company["name"]
        parse: Callable[[str, int], dict[str, str]] = company["parse"]
        try:
            slots = parse(target_date, party_size)
            result[name] = {
                "AM": slots.get("AM", "unknown"),
                "PM": slots.get("PM", "unknown"),
                "url": company["url"],
            }
        except Exception as exc:
            logging.exception("取得失敗: %s", name)
            errors.append(f"{name}: {exc}")
            result[name] = {
                "AM": "error",
                "PM": "error",
                "url": company["url"],
            }
    if len(errors) == len(COMPANIES):
        raise RuntimeError("全社の空き状況取得に失敗しました:\n" + "\n".join(errors))
    return result


def is_alertable(status: str, settings: Settings) -> bool:
    if status == "ok" and settings.alert_on_ok:
        return True
    if status == "limited" and settings.alert_on_limited:
        return True
    return False


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"alerted": {}}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def send_email(settings: Settings, subject: str, body: str) -> None:
    msg = MIMEMultipart()
    msg["From"] = settings.email_from
    msg["To"] = settings.email_to
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as server:
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(settings.smtp_user, settings.smtp_password)
        server.sendmail(settings.email_from, [settings.email_to], msg.as_string())


def format_snapshot(availability: dict[str, dict[str, str]], slot: str) -> str:
    lines = []
    for company in COMPANIES:
        name = company["name"]
        status = availability[name][slot]
        lines.append(
            f"- {name}: {STATUS_LABELS.get(status, status)}  {company['url']}"
        )
    return "\n".join(lines)


def build_alert_body(
    settings: Settings,
    hits: list[tuple[str, str, str]],
    availability: dict[str, dict[str, str]],
) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    hit_lines = "\n".join(
        f"・{name}: {STATUS_LABELS.get(status, status)}\n  予約: {url}"
        for name, status, url in hits
    )
    return (
        f"軍艦島ツアーに空きが出た可能性があります。\n\n"
        f"対象: {settings.target_date} {settings.target_slot} / {settings.party_size}名\n"
        f"検知時刻: {now}\n\n"
        f"【空き検知】\n{hit_lines}\n\n"
        f"【当日の全社状況】\n"
        f"{format_snapshot(availability, settings.target_slot)}\n\n"
        "※必ず各社の公式予約ページで確定してください。\n"
        "※残りわずかは2名分が取れない可能性もあります。急いで確認してください。"
    )


def check_once(settings: Settings, force_mail: bool = False) -> list[tuple[str, str, str]]:
    availability = fetch_availability(settings.target_date, settings.party_size)
    logging.info(
        "取得完了 %s %s\n%s",
        settings.target_date,
        settings.target_slot,
        format_snapshot(availability, settings.target_slot),
    )

    hits: list[tuple[str, str, str]] = []
    for company in COMPANIES:
        name = company["name"]
        status = availability[name][settings.target_slot]
        if is_alertable(status, settings):
            hits.append((name, status, company["url"]))

    state = load_state()
    alerted: dict = state.setdefault("alerted", {})
    key_prefix = f"{settings.target_date}:{settings.target_slot}"

    new_hits: list[tuple[str, str, str]] = []
    for name, status, url in hits:
        key = f"{key_prefix}:{name}"
        prev = alerted.get(key)
        if force_mail or prev != status:
            new_hits.append((name, status, url))
            alerted[key] = status

    for company in COMPANIES:
        name = company["name"]
        status = availability[name][settings.target_slot]
        key = f"{key_prefix}:{name}"
        if not is_alertable(status, settings) and key in alerted:
            del alerted[key]

    state["last_check"] = datetime.now().isoformat(timespec="seconds")
    state["last_snapshot"] = {
        company["name"]: availability[company["name"]][settings.target_slot]
        for company in COMPANIES
    }
    save_state(state)

    if new_hits:
        subject = (
            f"【軍艦島空き】{settings.target_date} {settings.target_slot} "
            f"{len(new_hits)}社で空きの可能性"
        )
        body = build_alert_body(settings, new_hits, availability)
        send_email(settings, subject, body)
        logging.info("メール送信: %s", ", ".join(name for name, _, _ in new_hits))
    else:
        logging.info("通知対象の新規空きなし（ヒット=%d）", len(hits))

    return new_hits


def run_loop(settings: Settings) -> None:
    logging.info(
        "監視開始: %s %s / %d名 / 間隔%d秒",
        settings.target_date,
        settings.target_slot,
        settings.party_size,
        settings.check_interval_seconds,
    )
    while True:
        try:
            check_once(settings)
        except Exception:
            logging.exception("チェック失敗（次回リトライ）")
        time.sleep(settings.check_interval_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="軍艦島ツアー空き監視")
    parser.add_argument("--once", action="store_true", help="1回だけチェックして終了")
    parser.add_argument("--test-mail", action="store_true", help="テストメールを送信して終了")
    parser.add_argument(
        "--force-mail",
        action="store_true",
        help="空きがある場合に状態重複でもメール送信",
    )
    args = parser.parse_args()
    setup_logging()
    settings = Settings.from_env()

    if args.test_mail:
        send_email(
            settings,
            "【軍艦島監視】テストメール",
            "メール設定は正常です。監視を開始できます。",
        )
        logging.info("テストメールを送信しました: %s", settings.email_to)
        return

    if args.once:
        check_once(settings, force_mail=args.force_mail)
        return

    run_loop(settings)


if __name__ == "__main__":
    main()
