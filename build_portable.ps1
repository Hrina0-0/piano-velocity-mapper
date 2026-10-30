<#
    build_portable.ps1 - 可选：生成"脱离项目目录"的独立安装

    作用：
      把程序装到  %LOCALAPPDATA%\Programs\PianoVelocityMapper
      这样即使把项目文件夹删了 / 移动了，桌面快捷方式依然能用。

    体积：约 6 MB（只含 mido + pygame；解释器复用本机已有的 Python）。
    注意：仍是 venv，所以目标机器上需要有 Python 3.10+；
          想要"拷到任何电脑都能跑"的绿色包，请用 PyInstaller 自行打包。

    用法：
      powershell -ExecutionPolicy Bypass -File build_portable.ps1
#>

$ErrorActionPreference = 'Stop'
chcp 65001 > $null

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$InstallDir = Join-Path $env:LOCALAPPDATA 'Programs\PianoVelocityMapper'
$AppDir     = Join-Path $InstallDir 'app'
$VenvDir    = Join-Path $InstallDir '.venv'
$VenvPyw    = Join-Path $VenvDir 'Scripts\pythonw.exe'
$VenvPy    = Join-Path $VenvDir 'Scripts\python.exe'

$Sources = @('app.py', 'gui.py', 'engine.py', 'velocity_mapper.py',
             'webui.py', 'webui.html', 'aa.py', 'tray.py', 'midi_ports.py',
             'paths.py', 'check_ports.py', 'app.ico', 'requirements.txt',
             'install_shortcut.py')

function Find-Python {
    foreach ($c in @('python', 'python3')) {
        $cmd = Get-Command $c -ErrorAction SilentlyContinue
        if ($cmd) {
            & $cmd.Source -c "import sys; sys.exit(0 if (3,10)<=sys.version_info<(3,15) else 1)" 2>$null
            if ($LASTEXITCODE -eq 0) { return $cmd.Source }
        }
    }
    $py = Get-Command 'py' -ErrorAction SilentlyContinue
    if ($py) {
        foreach ($v in @('3.13', '3.12', '3.11', '3.10')) {
            $exe = & py "-$v" -c 'import sys; print(sys.executable)' 2>$null
            if ($LASTEXITCODE -eq 0 -and $exe) { return $exe.Trim() }
        }
    }
    return $null
}

Write-Host '==> 查找 Python 3.10-3.14 ...'
$python = Find-Python
if (-not $python) {
    Write-Host '找不到合适的 Python。请先安装 Python 3.10+ (勾选 Add Python to PATH)。' -ForegroundColor Red
    exit 1
}
Write-Host "    使用: $python"

Write-Host '==> 创建独立安装目录 ...'
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
if (-not (Test-Path $VenvPy)) {
    & $python -m venv --copies $VenvDir
    if ($LASTEXITCODE -ne 0) { Write-Host '创建 venv 失败。' -ForegroundColor Red; exit 1 }
}

Write-Host '==> 安装依赖 ...'
& $VenvPy -m pip install --disable-pip-version-check -q -r requirements.txt
if ($LASTEXITCODE -ne 0) { Write-Host '安装依赖失败，请检查网络。' -ForegroundColor Red; exit 1 }

Write-Host '==> 复制程序文件 ...'
New-Item -ItemType Directory -Force -Path $AppDir | Out-Null
foreach ($s in $Sources) {
    $src = Join-Path $ProjectDir $s
    if (Test-Path $src) { Copy-Item $src -Destination $AppDir -Force }
}
# 把当前调好的配置带过去
if (Test-Path (Join-Path $ProjectDir 'config.json')) {
    Copy-Item (Join-Path $ProjectDir 'config.json') -Destination $AppDir -Force
}

Write-Host '==> 更新桌面快捷方式 ...'
& $VenvPy (Join-Path $AppDir 'install_shortcut.py') --portable

Write-Host '==> 自检 ...'
& $VenvPy -c "import tkinter, mido, sys; sys.path.insert(0, r'$AppDir'); import gui"
if ($LASTEXITCODE -ne 0) { Write-Host '自检未通过。' -ForegroundColor Red; exit 1 }

$size = '{0:N1} MB' -f ((Get-ChildItem $InstallDir -Recurse -File |
                          Measure-Object -Property Length -Sum).Sum / 1MB)
Write-Host ''
Write-Host "完成。独立安装目录: $InstallDir  ($size)" -ForegroundColor Green
Write-Host '桌面快捷方式已指向该目录，项目文件夹可以随意移动或删除。'
