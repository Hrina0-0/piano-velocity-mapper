# dev/ — 开发辅助脚本

这些脚本**不参与程序运行**，只是开发时用的小工具。发布包里不含这个目录。

| 脚本 | 作用 |
| --- | --- |
| `i18n_transform.py` | **中英双语批量转换脚本**。`PLAIN` / `FSTRINGS` 两张表里写着每条中文文案对应的英文；跑一次就把 `gui.py` / `engine.py` / `velocity_mapper.py` 里所有用户可见的字符串包成 `tr("中文", "English")` / `trf(f"…", f"…")`。新增文案时：把中文加进 `PLAIN`（f-string 加进 `FSTRINGS`），再跑一次转换脚本即可。 |
| `find_broken.py` | 扫描被转换脚本打断的「隐式字符串拼接」（原来相邻的两个字符串字面量，转换后变成两个函数调用，会报语法错）。 |
| `grab_window.py` | 抓取屏幕上一块区域存成 PNG（截界面图用）。 |
| `audit_release.py` | **发布前隐私审计**：把 `release/*.zip` 解开，逐文件扫个人标识（本机路径、用户名、设备型号、宿主软件名、API key、邮箱等），并检查有没有漏掉的 P115 残留。打包前跑一次。 |

## 用法

```powershell
# 转成双语
.\.venv\Scripts\python.exe dev\i18n_transform.py --write

# 检查有没有断行
.\.venv\Scripts\python.exe dev\find_broken.py

# 发布前审计
.\.venv\Scripts\python.exe dev\audit_release.py
```
