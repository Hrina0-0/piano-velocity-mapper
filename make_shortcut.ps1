# 在桌面创建「钢琴力度重映射」快捷方式
#
# 注意：本文件必须是 UTF-8 with BOM —— Windows PowerShell 5.1 只有看到 BOM
# 才会按 UTF-8 读脚本，否则中文会变成乱码，快捷方式名字就废了。

$ErrorActionPreference = 'Stop'

$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$exe = Join-Path $dir 'PianoVelocityMapper.exe'
if (-not (Test-Path $exe)) {
    Write-Host "找不到 $exe" -ForegroundColor Red
    exit 1
}

$desktop = [Environment]::GetFolderPath('Desktop')
$link = Join-Path $desktop '钢琴力度重映射.lnk'

$ws = New-Object -ComObject WScript.Shell
$sc = $ws.CreateShortcut($link)
$sc.TargetPath = $exe
$sc.WorkingDirectory = $dir
$sc.IconLocation = "$exe,0"
$sc.Description = '钢琴力度重映射：把键盘力度实时重映射到 1-127，经 loopMIDI 输出'
$sc.Save()

Write-Host "已创建桌面快捷方式: $link" -ForegroundColor Green
