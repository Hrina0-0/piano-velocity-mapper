# 把这个项目发布到 GitHub（给 AI 助手的说明）

> 这份文档是写给**另一个 AI 助手**看的：如果你在别的对话里想让助手自动把这个项目
> 传到 GitHub，把下面「可复制的指令」那一段贴给它就行。

---

## 可复制的指令

```
帮我把这个项目发布到 GitHub 仓库 <仓库地址>，要求：

1. 先读 docs/发布到GitHub.md，照着里面的流程做
2. 发布前必须做一次隐私审计（dev/audit_release.py）：
   确认打包内容里没有我的本机路径、用户名、设备型号、宿主软件名、API key、邮箱
3. 不要动我本地正在使用的那份原项目，所有改动只在打包用的副本里做
4. 只构建**一个**分发包（单文件 exe），不要再做源码包
5. 打 tag 发 Release，把这个包挂成 Release 附件
6. 发布完成后告诉我仓库地址、Release 地址和下载链接
```

---

## 完整流程（助手自己看）

### 0. 前置条件

* **gh CLI 已安装**：`winget install --id GitHub.cli --exact`
* **已登录**：`gh auth login`（浏览器授权一次，token 存进 Windows 凭据管理器）
* 仓库名、MIT 许可、公开可见性这些**事先问过用户**再动手

检查：

```powershell
gh auth status
```

### 1. 只在副本里干活

用户的本地项目往往是"自己正在用"的那份，**不要直接改它**。复制一份：

```powershell
Copy-Item -Recurse <原项目目录> <副本目录>
```

排除掉 `.venv`、`build`、`dist`、`release`、`__pycache__`（.gitignore 里已经写了）。

### 2. 隐私审计（最重要的一步）

用户要发给别人看的东西里最容易漏的就是他自己的信息。**打包前逐文件扫一遍**：

```powershell
.\.venv\Scripts\python.exe dev\audit_release.py
```

它会检查：用户名、本机路径、键盘型号、宿主软件名、邮箱、API key / token / 密码，
以及型号残留之类的东西。**有命中就改掉再打包**，别指望「反正没人会注意到」。

常见的坑：README 里顺手写了「我用 Cubase 做这个」「我的键盘是 XX」——这类**设备/软件
型号**也算个人痕迹，能泛化就泛化（"任意 DAW"、"小尺寸键床的 MIDI 键盘"）。

### 3. 构建

```powershell
.\.venv\Scripts\python.exe build_exe.py          # 单文件 exe
powershell -ExecutionPolicy Bypass -File make_release.ps1 -Version 1.3.2   # 一个分发包
```

### 4. 提交并推送

```powershell
git add -A
git commit -m "..."
git push origin main
```

### 5. 打 tag 发 Release

```powershell
git tag v1.3.2
git push origin v1.3.2
gh release create v1.3.2 --title "..." --notes-file <说明文件> PianoVelocityMapper-v1.3.2.zip
```

**附件名用 ASCII**：中文文件名在 `uploads.github.com` 上会 404。所以包名统一用
`PianoVelocityMapper-v<版本>.zip`，本地和 GitHub 上是同一个文件、同一个名字。

### 6. 仓库设置建议

```powershell
# 描述和话题标签（让人在搜索里能找到）
gh repo edit <仓库> --description "..." --add-topic midi --add-topic velocity ...
```

* 勾上 **Discussions** 和 **Issues**（Settings → General）
* README 放徽章、截图、功能表、FAQ、许可
* 仓库自带 `.github/workflows/*.yml` 的话，推 tag 就能自动打包发版

---

## 几个已经踩过的坑

| 坑 | 结论 |
| --- | --- |
| OAuth token 没有 `workflow` 权限时，推送含 `.github/workflows/` 的提交会被拒 | 先推代码，workflow 单独提交；或者让用户重新授权加上 `workflow` scope |
| Release 附件用中文名 → `uploads.github.com` 返回 404 | 附件名一律用 ASCII |
| Windows PowerShell 5.1 读无 BOM 的 `.ps1` 会把中文当 ANSI，脚本直接语法错 | 含中文的 `.ps1` 必须存成 **UTF-8 with BOM**；`.bat`/`.cmd` 反过来要**纯 ASCII** |
| Windows PowerShell 5.1 的 `Set-Content -Encoding UTF8` 会写 BOM，JSON 读出来会抛 `Unexpected UTF-8 BOM` | 用 `[System.IO.File]::WriteAllText($p, $text, (New-Object System.Text.UTF8Encoding($false)))` |
| `cmd.exe` 按控制台代码页解析 `.bat`，里面的中文会乱码甚至破坏行解析 | `.bat`/`.cmd` 里不放中文，中文挪进 UTF-8 BOM 的 `.ps1` |
| 打包出来的 `config.json` 带上了本机设备名 | 分发用的配置重置成出厂默认值 |

---

## 收尾检查清单

- [ ] `dev/audit_release.py` 报告「全部干净」
- [ ] 压缩包从解压目录能跑起来（配置写在 exe 旁边，不是临时目录）
- [ ] 从 GitHub 真实下载一次再运行，确认下载链路没问题
- [ ] 桌面快捷方式、图标、Release 附件都对（**Release 上只有一个包**）
- [ ] 用户本地那份没被动过
