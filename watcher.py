"""
軍艦島ツアー空き監視
長崎ツアーズの5社横断カレンダーを定期チェックし、空きが出たらメール通知します。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import smtplib
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
STATE_FILE = ROOT / "state.json"
LOG_FILE = ROOT / "watcher.log"
CALENDAR_URL = "https://nagasaki-tours.com/gunkanjima-tour-calendar"

COMPANIES = [
    {
        "name": "軍艦島コンシェルジュ",
        "url": "https://www.gunkanjima-concierge.com/cgi/web/?c=reserve-1",
    },
    {
        "name": "高島海上交通",
        "url": "https://www.gunkanjima-cruise.jp/reserve_input.php",
    },
    {
        "name": "やまさ海運",
        "url": "https://order.gunkan-jima.net/yamasa",
    },
    {
        "name": "シーマン商会",
        "url": "https://www.gunkanjima-tour-reserve.jp/reserve_input.php?course=1",
    },
    {
        "name": "第七ゑびす丸",
        "url": "https://mikata.in/nagasaki-tours/reservations/new?plan_id=2720",
    },
]

STATUS_LABELS = {
    "ok": "空きあり (o)",
    "limited": "残りわずか (△)",
    "cancel": "満席 (x)",
    "unknown": "不明",
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
                raise SystemExit(f".env に {key} を設定してください（.env.example を参照）")
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


def classify_cell(td) -> str:
    classes = td.get("class") or []
    if "status-ok" in classes:
        return "ok"
    if "status-limited" in classes:
        return "limited"
    if "status-cancel" in classes:
        return "cancel"
    text = td.get_text(strip=True).lower()
    if text in {"o", "〇", "○"}:
        return "ok"
    if text in {"△", "delta"}:
        return "limited"
    if text in {"x", "×"}:
        return "cancel"
    return "unknown"


def fetch_availability(target_date: str) -> dict[str, dict[str, str]]:
    """Return {company_name: {'AM': status, 'PM': status, 'url': ...}}"""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; GunkanjimaWatcher/1.0; "
            "+personal-availability-monitor)"
        )
    }
    response = requests.get(CALENDAR_URL, headers=headers, timeout=30)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "utf-8"

    soup = BeautifulSoup(response.text, "html.parser")
    row = None
    for tr in soup.find_all("tr"):
        first = tr.find("td")
        if first and first.get_text(strip=True) == target_date:
            row = tr
            break
    if row is None:
        raise RuntimeError(f"{target_date} の行が見つかりませんでした（カレンダー未掲載の可能性）")

    cells = row.find_all("td")[1:]
    if len(cells) < 10:
        raise RuntimeError(f"セル数が不足しています: {len(cells)}")

    result: dict[str, dict[str, str]] = {}
    for i, company in enumerate(COMPANIES):
        result[company["name"]] = {
            "AM": classify_cell(cells[i * 2]),
            "PM": classify_cell(cells[i * 2 + 1]),
            "url": company["url"],
        }
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
        lines.append(f"- {name}: {STATUS_LABELS.get(status, status)}  {company['url']}")
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
        f"横断カレンダー:\n{CALENDAR_URL}\n\n"
        "※長崎ツアーズの集計反映には遅れがある場合があります。"
        "必ず各社の公式予約ページで確定してください。\n"
        "※残りわずか(△)は2名分が取れない可能性もあります。急いで確認してください。"
    )


def check_once(settings: Settings, force_mail: bool = False) -> list[tuple[str, str, str]]:
    availability = fetch_availability(settings.target_date)
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
        # 同じステータスで連続通知しない。改善時は再通知。
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
    parser.add_argument(
        "--once",
        action="store_true",
        help="1回だけチェックして終了",
    )
    parser.add_argument(
        "--test-mail",
        action="store_true",
        help="テストメールを送信して終了",
    )
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
