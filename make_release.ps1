<#
    make_release.ps1 - 组装对外分发的压缩包

    产物（在 release\ 下）：
      钢琴力度重映射-绿色版.zip   单文件 exe，对方解压双击即用
      钢琴力度重映射-源码版.zip   完整源码，对方跑 setup.cmd 装环境

    用法：
      powershell -ExecutionPolicy Bypass -File make_release.ps1
      powershell -ExecutionPolicy Bypass -File make_release.ps1 -SkipBuild
#>

param([switch]$SkipBuild)

$ErrorActionPreference = 'Stop'
# 注意：脚本文件本身是 UTF-8 with BOM，Windows PowerShell 5.1 才能正确读中文。
# 不需要 chcp（那是 cmd.exe 的内建命令，PowerShell 里调不到）。

$Root      = Split-Path -Parent $MyInvocation.MyCommand.Path
$Release   = Join-Path $Root 'release'
$GreenDir  = Join-Path $Release '钢琴力度重映射-绿色版'
$SourceDir = Join-Path $Release '钢琴力度重映射-源码版'
$Exe       = Join-Path $Root 'dist\PianoVelocityMapper.exe'

function Remove-Dir($p) {
    if (Test-Path $p) { Remove-Item $p -Recurse -Force }
}

# ---------- 1. 打 exe ----------
if (-not $SkipBuild) {
    Write-Host '==> 打包绿色版 exe ...'
    $py = Join-Path $Root '.venv\Scripts\python.exe'
    if (-not (Test-Path $py)) { throw '没有 .venv，请先建虚拟环境并安装 pyinstaller。' }
    & $py (Join-Path $Root 'build_exe.py')
    if ($LASTEXITCODE -ne 0) { throw 'exe 打包失败。' }
}
if (-not (Test-Path $Exe)) { throw "找不到 exe：$Exe" }

# ---------- 2. 绿色版 ----------
Write-Host '==> 组装绿色版 ...'
Remove-Dir $GreenDir
New-Item -ItemType Directory -Force -Path $GreenDir | Out-Null
Copy-Item $Exe $GreenDir
Copy-Item (Join-Path $Root 'docs\使用说明-绿色版.md') (Join-Path $GreenDir '使用说明.md')
Copy-Item (Join-Path $Root 'docs\使用说明-绿色版-EN.md') (Join-Path $GreenDir 'README-EN.md')
Copy-Item (Join-Path $Root 'make_shortcut.cmd') (Join-Path $GreenDir '创建桌面快捷方式.cmd')
Copy-Item (Join-Path $Root 'make_shortcut.ps1') $GreenDir
Copy-Item (Join-Path $Root 'app.ico') $GreenDir
Copy-Item (Join-Path $Root 'third_party_licenses.txt') $GreenDir

# ---------- 3. 源码版 ----------
Write-Host '==> 组装源码版 ...'
Remove-Dir $SourceDir
New-Item -ItemType Directory -Force -Path $SourceDir | Out-Null

$SourceFiles = @(
    'app.py', 'gui.py', 'engine.py', 'velocity_mapper.py', 'aa.py', 'tray.py',
    'midi_ports.py', 'paths.py', 'i18n.py', 'check_ports.py', 'build_exe.py',
    'webui.py', 'webui.html',
    'app.ico', 'requirements.txt', 'setup.cmd', 'start_mapper.bat',
    'calibrate.bat', 'install_shortcut.py', 'build_portable.ps1', 'make_release.ps1',
    'README.md', 'README_EN.md'
)
foreach ($f in $SourceFiles) {
    $src = Join-Path $Root $f
    if (Test-Path $src) { Copy-Item $src $SourceDir } else { Write-Host "    跳过（不存在）: $f" -ForegroundColor Yellow }
}
# 配置给一份出厂默认值（不要把你本机调好的参数发出去）
Copy-Item (Join-Path $Root 'config.json') $SourceDir
# 注意：必须写「不带 BOM」的 UTF-8。Windows PowerShell 5.1 的
# Set-Content -Encoding UTF8 会加 BOM，会让 json.load 直接抛错。
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText(
    (Join-Path $SourceDir 'ui_prefs.json'),
    "{`n  `"close_action`": `"ask`",`n  `"minimize_to_tray`": true`n}`n",
    $utf8NoBom)
Copy-Item (Join-Path $Root 'docs\使用说明-源码版.md') (Join-Path $SourceDir '快速开始.md')
Copy-Item (Join-Path $Root 'docs\使用说明-源码版-EN.md') (Join-Path $SourceDir 'QUICKSTART-EN.md')
Copy-Item (Join-Path $Root 'third_party_licenses.txt') $SourceDir
# 详细文档 + 界面截图
$srcDocs = Join-Path $SourceDir 'docs'
New-Item -ItemType Directory -Force -Path $srcDocs | Out-Null
foreach ($d in @('详细说明.md', 'DETAILS_EN.md', 'screenshot-zh.png', 'screenshot-en.png')) {
    $p = Join-Path $Root "docs\$d"
    if (Test-Path $p) { Copy-Item $p $srcDocs -Force }
}

# ---------- 4. 压缩 ----------
Write-Host '==> 压缩 ...'
New-Item -ItemType Directory -Force -Path $Release | Out-Null
$greenZip  = Join-Path $Release '钢琴力度重映射-绿色版.zip'
$sourceZip = Join-Path $Release '钢琴力度重映射-源码版.zip'
foreach ($z in @($greenZip, $sourceZip)) { if (Test-Path $z) { Remove-Item $z -Force } }

Compress-Archive -Path (Join-Path $GreenDir '*')  -DestinationPath $greenZip  -CompressionLevel Optimal
Compress-Archive -Path (Join-Path $SourceDir '*') -DestinationPath $sourceZip -CompressionLevel Optimal

Write-Host ''
foreach ($z in @($greenZip, $sourceZip)) {
    $mb = '{0:N1} MB' -f ((Get-Item $z).Length / 1MB)
    Write-Host ("  {0,-40} {1}" -f (Split-Path $z -Leaf), $mb) -ForegroundColor Green
}
Write-Host ''
Write-Host "输出目录: $Release"
