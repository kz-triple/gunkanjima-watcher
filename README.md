# 軍艦島ツアー空き監視

2026-11-19 の AM 便を、5社横断カレンダーで定期確認し、空き（o）または残りわずか（△）が出たらメール通知します。

## 対象会社

| 会社 | 予約URL |
|---|---|
| 軍艦島コンシェルジュ | https://www.gunkanjima-concierge.com/cgi/web/?c=reserve-1 |
| 高島海上交通 | https://www.gunkanjima-cruise.jp/reserve_input.php |
| やまさ海運 | https://order.gunkan-jima.net/yamasa |
| シーマン商会 | https://www.gunkanjima-tour-reserve.jp/reserve_input.php?course=1 |
| 第七ゑびす丸 | https://mikata.in/nagasaki-tours/reservations/new?plan_id=2720 |

データソース: https://nagasaki-tours.com/gunkanjima-tour-calendar

## 本番運用: cron-job.org（推奨・確実）

GitHub 純正の `schedule` は遅延・スキップが多いため、**外部タイマーから10分ごとに起動**します。

### 1. GitHub トークンを作る

1. 開く: https://github.com/settings/personal-access-tokens/new
2. 設定例:
   - Token name: `gunkanjima-watcher-cron`
   - Expiration: `2026-11-20` など（ツアー後でOK）
   - Repository access: **Only select repositories** → `gunkanjima-watcher`
   - Permissions → Repository → **Actions: Read and write**
3. Generate して表示されたトークンをコピー（再表示不可）

### 2. cron-job.org でジョブ作成

1. https://cron-job.org で無料登録
2. **CREATE CRONJOB**
3. 以下を入力:

| 項目 | 値 |
|---|---|
| Title | `gunkanjima-watcher` |
| URL | `https://api.github.com/repos/kz-triple/gunkanjima-watcher/actions/workflows/check.yml/dispatches` |
| Schedule | Every 10 minutes（タイムゾーン Asia/Tokyo） |
| Request method | `POST` |
| Request body | `{"ref":"main"}` |

**Headers（Advanced）:**

| Key | Value |
|---|---|
| `Accept` | `application/vnd.github+json` |
| `Authorization` | `Bearer ` + 上で作ったトークン |
| `Content-Type` | `application/json` |
| `X-GitHub-Api-Version` | `2022-11-28` |

4. **Test Run** → ステータス 204 なら成功
5. Actions に `workflow_dispatch` が増えていればOK: https://github.com/kz-triple/gunkanjima-watcher/actions

### 3. メール Secrets（済ならスキップ）

| Name | Value |
|---|---|
| `SMTP_HOST` | `smtp.gmail.com` |
| `SMTP_PORT` | `587` |
| `SMTP_USER` | `hehuisongjing7@gmail.com` |
| `SMTP_PASSWORD` | Gmailアプリパスワード |
| `EMAIL_FROM` | `hehuisongjing7@gmail.com` |
| `EMAIL_TO` | `hehuisongjing7@gmail.com` |

---

## 補助: GitHub 純正 schedule

バックアップとして **1時間ごと** にも動きます（信頼度は低め）。メインは cron-job.org です。

## ローカル実行（任意）

```powershell
cd $env:USERPROFILE\Projects\gunkanjima-watcher
.\.venv\Scripts\Activate.ps1
python watcher.py --test-mail
python watcher.py --once
```

## 注意

- カレンダー反映には遅れがある場合があります。メールが来たら各社の公式サイトで即予約してください。
- △（残りわずか）は2名分が確保できない可能性もあります。
- 同じ空き状態では再通知しません。満席に戻って再び空いた場合は再通知します。
- PAT の有効期限が切れると外部起動が止まるので、期限前に更新してください。
