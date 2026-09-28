# cron-job.org から GitHub Actions を起動するときの設定メモ
# （トークンはここに書かないこと）

$DispatchUrl = "https://api.github.com/repos/kz-triple/gunkanjima-watcher/actions/workflows/check.yml/dispatches"

# 使い方:
#   $env:GH_PAT = "github_pat_xxxx"
#   .\scripts\trigger-once.ps1

if (-not $env:GH_PAT) {
    Write-Error "先に `$env:GH_PAT` に fine-grained token をセットしてください"
    exit 1
}

$headers = @{
    Accept = "application/vnd.github+json"
    Authorization = "Bearer $($env:GH_PAT)"
    "X-GitHub-Api-Version" = "2022-11-28"
}

Invoke-RestMethod -Method Post -Uri $DispatchUrl -Headers $headers -ContentType "application/json" -Body '{"ref":"main"}'
Write-Host "Triggered. Check: https://github.com/kz-triple/gunkanjima-watcher/actions"
