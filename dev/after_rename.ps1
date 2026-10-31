<#
.SYNOPSIS
    GitHub 账号改名后的收尾脚本。

.DESCRIPTION
    改名（用户名变更）完成后执行本脚本，把本地仓库和账号状态同步到新用户名：
      1. 更新 git remote origin 地址
      2. 更新仓库级提交邮箱为带数字 ID 的新格式（改名后署名仍能关联账号）
      3. 校验 Release 附件在新用户名下仍可下载
      4. 校验旧提交是否仍归属于本账号

.PARAMETER NewName
    改名后的新用户名（不含 @）。

.PARAMETER Repo
    本地仓库路径，默认 D:\Codex-work\repiano_pkg。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File dev\after_rename.ps1 -NewName Hrina00
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$NewName,
    [string]$Repo = 'D:\Codex-work\repiano_pkg'
)

$ErrorActionPreference = 'Stop'
$gh = "$env:ProgramFiles\GitHub CLI\gh.exe"
$git = 'git'
$RepoName = 'piano-velocity-mapper'
$OldName = 'lanminhua666-dotcom'

Write-Host ''
Write-Host '=== GitHub 用户名改名收尾 ===' -ForegroundColor Cyan
Write-Host "旧用户名 : $OldName"
Write-Host "新用户名 : $NewName"
Write-Host "仓库     : $Repo"
Write-Host ''

# ---- 1. 确认改名确实生效 -------------------------------------------------
$login = (& $gh api user --jq '.login').Trim()
if ($login -ne $NewName) {
    Write-Host "[跳过] 账号当前登录名是 '$login'，尚未改名为 '$NewName'。" -ForegroundColor Yellow
    Write-Host '       改名未完成时不改动本地仓库，避免 remote 指到不存在的路径。' -ForegroundColor Yellow
    exit 1
}
Write-Host "[1/4] 账号已改名为 $login" -ForegroundColor Green

# ---- 2. 更新 remote -----------------------------------------------------
$newRemote = "https://github.com/$NewName/$RepoName.git"
$oldRemote = (& $git -C $Repo remote get-url origin 2>$null)
Write-Host "       旧 remote : $oldRemote"
& $git -C $Repo remote set-url origin $newRemote
Write-Host "[2/4] remote origin -> $newRemote" -ForegroundColor Green

# ---- 3. 更新提交邮箱为带 ID 的新格式 ------------------------------------
# 数字 ID 是账号的稳定标识：即使以后再改名，提交署名依然能关联到账号。
$acctId = (& $gh api user --jq '.id').Trim()
$newEmail = "${acctId}+$NewName@users.noreply.github.com"
& $git -C $Repo config user.email $newEmail
$cur = (& $git -C $Repo config user.email).Trim()
Write-Host "[3/4] 提交邮箱 -> $cur" -ForegroundColor Green

# ---- 4. 校验 Release 附件在新用户名下可下载 -------------------------------
Write-Host "[4/4] 校验 Release 附件与提交归属..." -ForegroundColor Green

$relUrl = "https://github.com/$NewName/$RepoName/releases/latest"
try {
    $r = Invoke-WebRequest -Uri $relUrl -Method Head -MaximumRedirection 5 -UseBasicParsing -TimeoutSec 30 -ErrorAction Stop
    Write-Host "       Release 页 HTTP $($r.StatusCode)" -ForegroundColor DarkGray
} catch {
    Write-Host "       Release 页访问失败: $($_.Exception.Message)" -ForegroundColor Yellow
}

# 附件下载：走 API 取真实 URL，避免猜错
$assets = & $gh release list --repo "$NewName/$RepoName" --limit 5 --json tagName --jq '.[].tagName' 2>$null
if ($assets) {
    $tag = $assets[0]
    $dl = & $gh release view $tag --repo "$NewName/$RepoName" --json assets --jq '.assets[0].url' 2>$null
    if ($dl) {
        try {
            $r2 = Invoke-WebRequest -Uri $dl -Method Head -MaximumRedirection 5 -UseBasicParsing -TimeoutSec 60 -ErrorAction Stop
            Write-Host "       附件下载 HTTP $($r2.StatusCode)  ($dl)" -ForegroundColor DarkGray
        } catch {
            Write-Host "       附件下载失败: $($_.Exception.Message)" -ForegroundColor Yellow
        }
    }
}

$linked = & $gh api "repos/$NewName/$RepoName/commits" --jq '.[].author.login // "NONE"' 2>$null |
    Sort-Object -Unique
Write-Host "       提交署名归属: $($linked -join ', ')" -ForegroundColor DarkGray

Write-Host ''
Write-Host '=== 完成 ===' -ForegroundColor Cyan
Write-Host "仓库地址 : https://github.com/$NewName/$RepoName"
Write-Host "Release  : https://github.com/$NewName/$RepoName/releases"
