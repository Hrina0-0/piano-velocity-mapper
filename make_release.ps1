<#
    make_release.ps1 - 组装对外分发的压缩包（只有一个）

    产物（在 release\ 下）：
      PianoVelocityMapper-v<版本>.zip   单文件 exe + 使用说明，对方解压双击即用

    用法：
      powershell -ExecutionPolicy Bypass -File make_release.ps1
      powershell -ExecutionPolicy Bypass -File make_release.ps1 -SkipBuild
      powershell -ExecutionPolicy Bypass -File make_release.ps1 -Version 1.3.0
#>

param([switch]$SkipBuild, [string]$Version = "")

$ErrorActionPreference = 'Stop'
# 注意：脚本文件本身是 UTF-8 with BOM，Windows PowerShell 5.1 才能正确读中文。
# 不需要 chcp（那是 cmd.exe 的内建命令，PowerShell 里调不到）。

$Root    = Split-Path -Parent $MyInvocation.MyCommand.Path
$Release = Join-Path $Root 'release'
$PackDir = Join-Path $Release 'PianoVelocityMapper'
$Exe     = Join-Path $Root 'dist\PianoVelocityMapper.exe'

# 版本号：命令行给的优先，否则从最近的 git tag 推断
if (-not $Version) {
    $Version = '0.0.0'
    try {
        $tag = (& git -C $Root describe --tags --abbrev=0 2>$null | Select-Object -First 1)
        if ($tag) { $Version = ($tag -replace '^v', '') }
    } catch {
        # 没有 git 或没有 tag 就用 0.0.0
    }
}
$ZipName = "PianoVelocityMapper-v$Version.zip"
$ZipPath = Join-Path $Release $ZipName

function Remove-Dir($p) {
    if (Test-Path $p) { Remove-Item $p -Recurse -Force }
}

# ---------- 1. 打 exe ----------
if (-not $SkipBuild) {
    Write-Host '==> 打包 exe ...'
    $py = Join-Path $Root '.venv\Scripts\python.exe'
    if (-not (Test-Path $py)) { throw '没有 .venv，请先建虚拟环境并安装 pyinstaller。' }
    & $py (Join-Path $Root 'build_exe.py')
    if ($LASTEXITCODE -ne 0) { throw 'exe 打包失败。' }
}
if (-not (Test-Path $Exe)) { throw "找不到 exe：$Exe" }

# ---------- 2. 组装 ----------
Write-Host "==> 组装 $ZipName ..."
Remove-Dir $PackDir
New-Item -ItemType Directory -Force -Path $PackDir | Out-Null
Copy-Item $Exe $PackDir
Copy-Item (Join-Path $Root 'docs\使用说明.md')         (Join-Path $PackDir '使用说明.md')
Copy-Item (Join-Path $Root 'docs\使用说明-EN.md')      (Join-Path $PackDir 'README-EN.md')
Copy-Item (Join-Path $Root 'make_shortcut.cmd')        (Join-Path $PackDir '创建桌面快捷方式.cmd')
Copy-Item (Join-Path $Root 'make_shortcut.ps1')        $PackDir
Copy-Item (Join-Path $Root 'app.ico')                  $PackDir
Copy-Item (Join-Path $Root 'third_party_licenses.txt') $PackDir

# ---------- 3. 压缩（release\ 里只保留这一个包）----------
Write-Host '==> 压缩 ...'
New-Item -ItemType Directory -Force -Path $Release | Out-Null
Get-ChildItem $Release -Filter '*.zip' -File -ErrorAction SilentlyContinue | ForEach-Object {
    Write-Host "    清理旧包: $($_.Name)"
    Remove-Item $_.FullName -Force
}
Compress-Archive -Path (Join-Path $PackDir '*') -DestinationPath $ZipPath -CompressionLevel Optimal

# 临时目录用完就清掉，release\ 里只留这一个 zip
Remove-Dir $PackDir

$mb = '{0:N1} MB' -f ((Get-Item $ZipPath).Length / 1MB)
Write-Host ''
Write-Host ("  {0,-40} {1}" -f $ZipName, $mb) -ForegroundColor Green
Write-Host ''
Write-Host "输出目录: $Release"
