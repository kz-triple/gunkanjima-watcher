# 軍艦島ツアー空き監視

2026-11-19 の AM 便を、5社横断カレンダーで10分ごとに確認し、空き（o）または残りわずか（△）が出たらメール通知します。

## 対象会社

| 会社 | 予約URL |
|---|---|
| 軍艦島コンシェルジュ | https://www.gunkanjima-concierge.com/cgi/web/?c=reserve-1 |
| 高島海上交通 | https://www.gunkanjima-cruise.jp/reserve_input.php |
| やまさ海運 | https://order.gunkan-jima.net/yamasa |
| シーマン商会 | https://www.gunkanjima-tour-reserve.jp/reserve_input.php?course=1 |
| 第七ゑびす丸 | https://mikata.in/nagasaki-tours/reservations/new?plan_id=2720 |

データソース: https://nagasaki-tours.com/gunkanjima-tour-calendar

## おすすめ運用: GitHub Actions（PC不要・無料）

PCをつけっぱなしにしなくても、GitHub上で10分ごとに自動チェックします。

### 1. GitHubにリポジトリを作る

**Public（公開）推奨**（無料アカウントでもスケジュール実行できるため）。  
パスワードはコードに入れず GitHub Secrets に置くので、公開でも安全です。

```powershell
cd $env:USERPROFILE\Projects\gunkanjima-watcher
gh repo create gunkanjima-watcher --public --source=. --remote=origin --push
```

`gh` がなければ、GitHubサイトで空のリポジトリを作り `git remote add` → `git push` でもOKです。

### 2. Secrets を登録

リポジトリ → **Settings → Secrets and variables → Actions → New repository secret** で以下を追加:

| Name | Value |
|---|---|
| `SMTP_HOST` | `smtp.gmail.com` |
| `SMTP_PORT` | `587` |
| `SMTP_USER` | `hehuisongjing7@gmail.com` |
| `SMTP_PASSWORD` | Gmailアプリパスワード |
| `EMAIL_FROM` | `hehuisongjing7@gmail.com` |
| `EMAIL_TO` | `hehuisongjing7@gmail.com` |

### 3. 動作確認

**Actions** タブ → **Gunkanjima availability check** → **Run workflow** で手動実行。  
成功すれば、以降は約10分ごとに自動実行されます。

---

## ローカル実行（任意）

```powershell
cd $env:USERPROFILE\Projects\gunkanjima-watcher
.\.venv\Scripts\Activate.ps1
copy .env.example .env   # 初回のみ
# .env を編集してから:
python watcher.py --test-mail
python watcher.py --once
```

## 注意

- カレンダー反映には遅れがある場合があります。メールが来たら各社の公式サイトで即予約してください。
- △（残りわずか）は2名分が確保できない可能性もあります。
- 同じ空き状態では再通知しません。満席に戻って再び空いた場合は再通知します。
- GitHubの定期実行は数分遅れることがあります（目安10〜15分間隔）。
- 公開リポジトリのスケジュールは、長期間リポジトリに動きがないと止まることがあります。たまに Actions を確認してください。
