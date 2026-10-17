# Win11 任务栏歌词 + 网易云高赞热评 + 手机端跨端待办（v1.2.2）

<img src="desktop/logo.png" alt="项目 Logo" width="96" align="right" />

> Logo：磨砂玻璃音乐清单 · 已用于 exe 图标、托盘/窗口图标与手机 PWA 图标。
>
> 📦 Windows 免安装版下载：<https://github.com/luongthimydieu/win11-taskbar-lyrics/releases/tag/v1.2.2>

把 Windows 11 任务栏的空白处变成一条“长在任务栏上”的信息条：主行跟着网易云音乐滚动逐行歌词，副行轮播**精选的有意思热评**与**下一项待办**；到点提醒时整条变红并呼吸闪烁，左键一点就完成并回写云端。手机端是一个可添加到主屏幕的静态 PWA，在外面随手记一条待办，电脑端 30 秒内就能看到。

v1.2.1 起桌面端默认使用「置顶覆盖 + 任务栏背景截图」把文字贴合在任务栏左侧空位（不遮图标、整块可点击），并把 `SetParent` 嵌入保留为实验选项；v1.2.2 加入每日待办节奏（22:00 提醒填明天、凌晨 2:00 自动翻篇）。

整个项目**不需要自建服务器、不需要域名、不需要数据库**：手机端跑在 GitHub Pages 上，数据放在你自己账号的一个私密 GitHub Gist 里，Windows 端用 PyQt6 读取并轮询。

## 项目亮点

| 亮点 | 说明 |
| --- | --- |
| 零后端 | 手机端是纯静态页面，Gist 充当免费云数据库，GitHub Pages 托管前端，没有服务器要运维 |
| 任务栏原生感 | 无边框、不进 Alt+Tab；默认**置顶覆盖 + 任务栏背景截图**，底色与任务栏逐像素一致、自动贴左侧空位并避开图标；右键可切「嵌入任务栏」（实验性） |
| 歌词 + 热评 | 通过网易云非官方接口取 LRC 与热赞评论；接口失败只降级这两项，不影响待办与提醒 |
| 有意思的热评 | 从 hotComments 里过滤水评/广告/纯表情，优先挑“有故事/有情绪”的短评，轮播更慢、每首只留约 10 条 |
| 只显示下一项待办 | 按你在手机端上传的顺序稳定显示“下一项”，完成一条自动顺延；不再在待办之间随机轮播 |
| 每日待办节奏 | **22:00 之后添加的待办自动算“明天”**；**凌晨 2:00 自动翻篇**到新的一天（0:00–1:59 仍算前一天）；**每天 22:00 电脑托盘提醒**你填写明天的待办（右键可关），手机端 22:00 后也会显示“明天的待办还没填”的提示 |
| 不丢数据的同步 | schema v2：条目级 `updated_at` + 删除墓碑 `tombstones` + `version`，两端同一套 `mergeDocs`，写前先合并 |
| 省额度的轮询 | 每 30 秒带 `If-None-Match` 请求，304 不计入 GitHub 限流额度 |
| 可靠的到点提醒 | `remind_date` + 目标时刻 + 24 小时补提醒窗口 + 云端 `notified_at` 去重 + 稍后提醒 10 分钟 |
| 双媒体后端 | 自动识别 `winsdk`（Python ≤3.11）或 `winrt-Windows.Media.Control`（Python 3.12+），都没有时退回窗口标题 |
| 可测试 | `core.py` 不依赖 Qt，55 个 Python 单测 + 20 个前端协议用例；`tests/run_all.ps1` 可一键全跑 |
| 工程化细节 | 单实例互斥量、系统托盘、滚动日志、原子写配置、每 5 秒检测任务栏几何变化 |
| 可诊断 | 右键菜单「🩺 任务栏诊断」或 `--diagnose`，直接给出 Windows 版本、任务栏句柄、当前嵌入状态与子窗口结构 |

## 组成与技术栈

| 部分 | 技术 | 入口 |
| --- | --- | --- |
| Windows 任务栏端 | Python 3.10+ / PyQt6 / Win32 API / Windows SMTC | `desktop/taskbar_widget.py` |
| 无界面核心层 | Python 标准库 + `requests` | `desktop/core.py` |
| 手机端 PWA | 原生 HTML/CSS/JS + Service Worker + GitHub REST API | `github-pages/index.html` |
| 云数据库 | 私密 GitHub Gist 中的 `taskbar_todos.json` | 由两端自动创建或手动填写 |
| 测试与工具 | `unittest`、Node `vm`、Pillow | `tests/`、`tools/make_icons.py` |

## 目录结构

```text
win11-taskbar-lyrics/
├── README.md
├── docs/                  # 专题文档 01–06（连本 README 共 7 篇）
├── desktop/               # Windows 任务栏端（Python + PyQt6）
│   ├── core.py            # 无 Qt 依赖的核心层，可单元测试
│   ├── taskbar_widget.py  # UI / SMTC / 任务栏吸附 / 提醒
│   ├── requirements.txt
│   ├── install_deps.bat / run.bat
│   ├── build_exe.ps1 / install_autostart.ps1
├── github-pages/          # 需要推到 GitHub Pages 的那部分（部署包）
│   ├── index.html / manifest.webmanifest / sw.js
│   ├── icon-192.png / icon-512.png / apple-touch-icon.png
│   ├── .nojekyll / README.md
├── tests/                 # test_core.py（Python 单测）+ test_web_protocol.js（Node 协议测试）+ run_all.ps1（一键验证）
├── tools/make_icons.py    # 生成 PWA 图标
└── reference/             # v1.0 原始文件存档（仅供对照）
```

## 三步快速开始

> 完整步骤、每一步的验证方法与两条 Python 环境路线见 [部署指南](docs/02-部署指南.md)。

### 第 1 步：部署手机端

1. 新建一个 GitHub 仓库（公开仓库的 Pages 免费），把 **`github-pages/` 目录里的文件**推送到仓库根目录；
2. 仓库 **Settings → Pages → Deploy from a branch → main / (root)**，保存；
3. 等 1–2 分钟，记下 `https://<你的用户名>.github.io/<仓库名>/`。

细节（含 `.nojekyll`、私密仓库 Pages 限制、更新部署）见 [github-pages/README.md](github-pages/README.md)。

> 为了方便交付，`github-pages/` 目录已经初始化好本地 Git 仓库并完成一次提交：你只需要 `git remote add origin <仓库地址>`，然后 `git push -u origin main`。想先把提交作者改成自己，可在该目录执行 `git config user.name/user.email` 后 `git commit --amend --reset-author --no-edit`。

### 第 2 步：安装并启动电脑端

在 **Anaconda Prompt** 中进入项目目录（本机 `python` 不在 PATH，必须用 Anaconda Prompt）：

```bat
cd /d "G:\deepseek harness\win11-taskbar-lyrics\desktop"
install_deps.bat
run.bat
```

> 本机已经为这个项目创建好**专用 conda 环境 `taskbar-lyrics`**（Python 3.11.15 + PyQt6 6.11.0 + requests + winsdk，装好即用），推荐这样启动：

```bat
conda activate taskbar-lyrics
cd /d "G:\deepseek harness\win11-taskbar-lyrics\desktop"
python taskbar_widget.py
```

> 上面是 **CMD / Anaconda Prompt** 写法。如果你用的是 **PowerShell**：`cd /d` 会报“找不到接受实际参数”，把 `/d` 去掉即可 —— `cd "G:\deepseek harness\win11-taskbar-lyrics\desktop"`，或者不切目录直接 `python "G:\deepseek harness\win11-taskbar-lyrics\desktop\taskbar_widget.py"`。

换机器或环境被删时，`install_deps.bat` 会装到**当前激活的 Python 环境**；也可以按版本手动重建路线（两条都实测可行）：

```bat
:: 路线 A：Python 3.10–3.11（winsdk 有对应轮子）
conda create -n taskbar-lyrics python=3.11 -y
conda activate taskbar-lyrics
cd /d "G:\deepseek harness\win11-taskbar-lyrics\desktop"
python -m pip install -r requirements.txt
python taskbar_widget.py
```

```bat
:: 路线 B：Python 3.13（winsdk 无 cp313 轮子，改用 pywinrt 拆分包）
python -m pip install PyQt6 requests winrt-Windows.Media.Control
python taskbar_widget.py
```

启动后任务栏上会出现胶囊；没有配置云同步时，它会先显示本机待办与歌词。无界面自检：

```bat
python taskbar_widget.py --selftest
```

### 第 3 步：两端绑定同一组 Token + Gist ID

1. 手机浏览器打开 Pages 地址 → 右上角「未配置」→ 填 GitHub Token（classic，勾选 `gist`）→ 点「创建新 Gist」→「保存并同步」；
2. 在电脑端右键任务栏胶囊 →「⚙️ 配置 GitHub Gist 云同步」→ 填**同一个 Token 和 Gist ID** →「测试连接」→ 保存；
3. 手机端加一条待办，等 30 秒（或右键「🔄 立即同步」），胶囊副行就会出现它。

Token 只在两端本地保存：电脑端在 `%APPDATA%\Win11TaskbarLyrics\config.json`，手机端在浏览器 `localStorage`，都不会写进 Git 仓库。

## 常用命令

以下命令默认在项目根目录执行；标注 **CMD** 的用命令提示符/`.bat`，标注 **PowerShell** 的用 PowerShell。找不到 `python` 时先打开 Anaconda Prompt 并激活环境。

| 用途 | 命令 | 环境 |
| --- | --- | --- |
| 启动任务栏胶囊 | `python desktop\taskbar_widget.py` | CMD / PowerShell |
| 前台启动并保留控制台 | `desktop\run.bat` | CMD |
| 安装桌面端依赖 | `desktop\install_deps.bat` | CMD（Anaconda Prompt） |
| 无界面烟测 | `python desktop\taskbar_widget.py --selftest` | CMD |
| 任务栏诊断 | `python desktop\taskbar_widget.py --diagnose` | CMD（报“没嵌进去/位置不对”时先跑这个） |
| 核心层单元测试 | `python -m unittest discover -s tests -v` | CMD |
| 前端同步协议测试 | `node tests/test_web_protocol.js` | CMD（Node 18+） |
| 一键跑全部离线验证 | `powershell -ExecutionPolicy Bypass -File tests\run_all.ps1` | PowerShell（5.1+ 实测可用） |
| 重新生成 PWA 图标 | `python tools\make_icons.py` | CMD |
| 用 AI 生成的 PNG 生成 PWA 图标 | `python tools\make_icons.py --source logo.png` | CMD |
| PNG 转多尺寸 .ico（exe/快捷方式图标） | `python tools\make_ico.py desktop\logo.png desktop\logo.ico` | CMD |
| 打包成 exe | `powershell -ExecutionPolicy Bypass -File desktop\build_exe.ps1` | PowerShell（已内置 `logo.ico`，自动带图标） |
| 打包成 exe（自定义图标） | `powershell -ExecutionPolicy Bypass -File desktop\build_exe.ps1 -Icon desktop\logo.ico` | PowerShell |
| 单文件 exe | `powershell -ExecutionPolicy Bypass -File desktop\build_exe.ps1 -OneFile` | PowerShell |
| 设置开机自启 | `powershell -ExecutionPolicy Bypass -File desktop\install_autostart.ps1` | PowerShell |
| 取消开机自启 | `powershell -ExecutionPolicy Bypass -File desktop\install_autostart.ps1 -Uninstall` | PowerShell |
| 打开数据目录 | `explorer %APPDATA%\Win11TaskbarLyrics` | CMD |
| 查看日志尾部 | `Get-Content "$env:APPDATA\Win11TaskbarLyrics\app.log" -Tail 50 -Wait` | PowerShell |
| 彻底重置本地 | `Remove-Item -Recurse -Force "$env:APPDATA\Win11TaskbarLyrics"` | PowerShell |

> `node tests/test_web_protocol.js` 会逐条打印用例结果（当前 20 个全部通过）；只要有失败，进程退出码就是 1，可直接作为 CI 门禁。`tests/run_all.ps1` 会把它和 Python 单测、语法检查、无界面烟测一起跑。
>
> 重置前请先退出程序；删除 `%APPDATA%\Win11TaskbarLyrics` 只会清掉本机配置、缓存与日志，**不会删除云端 Gist 里的待办**。

## 验证结果（2026-10-17 本机实测）

| 检查项 | 命令 | 结果 |
| --- | --- | --- |
| 桌面端语法编译 | `python -m py_compile desktop/core.py desktop/taskbar_widget.py` | 通过 |
| 静态检查 | `python -m pyflakes desktop/core.py desktop/taskbar_widget.py tests/test_core.py tools/make_icons.py` | 0 告警 |
| 核心层单元测试 | `python -m unittest discover -s tests -v` | 63 个用例通过（含“每日待办节奏”边界用例） |
| 前端同步协议测试 | `node tests/test_web_protocol.js` | 24 个用例通过（含 DOM 引用、SW 资源静态检查与每日节奏用例） |
| 跨语言协议一致性 | 同一份文档分别交给 `core.canonical` 与 `P.canonical` | 3 份文档指纹逐字节一致 |
| 热评筛选（真实接口） | `core.NetEaseClient.fetch_song_bundle(...)` | 38 行歌词 + 10 条精选评论，水评/广告被过滤 |
| 任务栏显示（真机） | 启动后截图 + `last_text.json` | 置顶覆盖模式，窗口 17–577、白色文字显示在任务栏左侧空位、不遮图标 |
| 实时刷新（真机） | 间隔 8 秒两次截图 | 歌词从 `And all the clubs…` 变为 `You think you broke my heart…`，副行在“精选热评”与“📋 下一项待办”之间切换 |
| 热评筛选（真机） | `last_text.json` 的 secondary | 显示「加拿大有三宝…」等精选评论，水评/广告被过滤 |
| 每日节奏（实测） | Python/JS 单测 + 手动验证 | 21:59 添加算当天、22:00 添加算明天、次日 2:00 前仍显示前一天、22:05 未填明天时返回提醒/显示提示 |
| exe 打包（真机） | `build_exe.ps1`（onedir） | 产物 `desktop\dist\Win11TaskbarLyrics\` 约 130.5 MB；`Win11TaskbarLyrics.exe --selftest` 退出码 0 |
| exe 运行（真机） | 安装到 `%LOCALAPPDATA%\Programs\Win11TaskbarLyrics` 并启动 | 任务栏正常显示白字歌词/下一项；约 153 MB 工作集、81 MB 私有内存、14 线程、1180 句柄，空闲 CPU <1% |
| 开机自启（真机） | `install_autostart.ps1 -ExePath ...` | 启动文件夹快捷方式指向安装目录 exe、工作目录正确；下次登录自动启动 |
| Service Worker 语法 | `node --check github-pages/sw.js` | 通过 |
| 无界面烟测 | `python desktop/taskbar_widget.py --selftest` | `MEDIA_BACKEND=winsdk` + `SELFTEST OK`（exit 0） |
| 一键验证脚本 | `powershell -ExecutionPolicy Bypass -File tests\run_all.ps1` | 全部验证通过（Windows PowerShell 5.1 实测） |

烟测环境：Windows 11、Python 3.11.15 + PyQt6 6.11.0 + winsdk 1.0.0b10（本机为本项目创建的专用 conda 环境 `taskbar-lyrics`），Node v24.18.0。
说明：Anaconda base 的 Python 3.13.9 没有 PyQt6/winsdk；走 3.13 路线请用 `winrt-Windows.Media.Control`（已验证存在 cp313 轮子）。

## 文档索引

| 文档 | 内容 |
| --- | --- |
| [01 架构设计](docs/01-架构设计.md) | 数据流、模块分层、同步/提醒时序、关键取舍、安全边界 |
| [02 部署指南](docs/02-部署指南.md) | 从零创建 Token、部署 Pages、两条 Python 路线、绑定、自启、升级与重置 |
| [03 同步协议](docs/03-同步协议.md) | JSON schema、合并算法、canonical、ETag/HTTP、错误码、收敛举例、提醒语义 |
| [04 常见问题](docs/04-常见问题.md) | 歌词/热评、媒体后端、任务栏吸附、同步报错、Token 吊销、限流等 |
| [05 更新日志](docs/05-更新日志.md) | v1.2.2 / v1.2.1 / v1.2.0 / v1.1.0 变更对照与升级注意事项 |
| [06 打包与开机自启](docs/06-打包与开机自启.md) | `build_exe.ps1`、`install_autostart.ps1`、启动文件夹原理、打包排错 |
| [Logo 提示词](docs/LOGO_PROMPT.md) | 给 GPT / DALL·E 的项目图标、横幅提示词，以及 ICO / PWA 图标生成步骤 |
| [07 发布 exe](docs/07-发布exe到GitHub.md) | 把打包好的 zip 发到 GitHub Releases 的三种方法与发布前验证 |

v1.0 的原始文件保存在 `reference/`（`original_taskbar_widget.py`、`original_index.html`），只用于对照，不参与运行。

## 已知限制

- **没有服务器推送**：电脑端靠 30 秒轮询，手机端靠 60 秒定时 + 回到前台/窗口聚焦/网络恢复时触发，所以跨端看到变化有秒级到一分钟级延迟；Gist 没有 webhook 可用。
- **Gist 私密不等于加密**：私密 Gist 只是不公开列出、不出现在搜索里；知道 Gist 链接的人仍可能查看，GitHub 与你账号下有权者也能读取。不要把密码、密钥、身份证号等敏感信息写进待办。
- **任务栏空间是动态的**：默认“置顶覆盖 + 任务栏背景截图”，底色与任务栏逐像素一致但不是透明窗口；程序每 5 秒检测开始按钮/任务按钮/托盘的安全区，图标挤过来会自动缩窄并换到新空位，空间完全不够时会暂时隐藏并在托盘提示、腾出空间后自动恢复。全屏独占游戏会遮住它；任务栏自动隐藏时不会跟隐；“嵌入任务栏”为实验选项（部分 Windows 11 版本上跨进程子窗口不重绘）；不支持副屏任务栏。
- **网易云接口非官方**：搜索、歌词、热评接口随时可能变更或限流，失败时只显示歌名，不影响待办、同步和提醒。
- **媒体后端不是万能的**：没有 `winsdk`/`pywinrt` 时只能读网易云窗口标题，歌词按单调时钟估算推进，暂停或拖动进度条后可能对不上；装了 SMTC 后端才会精确跟随。
- **仅支持 Windows 11**：任务栏矩形、SMTC、命名互斥量都是 Windows API；手机端需要支持 Service Worker 的现代浏览器（iOS Safari、Android Chrome 均可）。
- **数据规模**：Gist 单文件读取超过 1MB 会被 GitHub 截断，程序会报「内容被截断」，建议定期「清除已完成」。
- **资源占用**：PyQt6 桌面端常驻约 **150 MB 工作集**（Qt6 是主要部分，私有内存约 80 MB），空闲 CPU 约 <1%；打包 exe 与源码版基本一致（onedir 目录约 130 MB）。内存特别紧张时可改用更轻的 GUI 方案，但 Qt 本身占大头，压缩空间有限。
- **混用版本会丢墓碑**：v1.0 不认识 `tombstones`，v1.0 与 v1.1 客户端同时写同一个 Gist 时，删除记录可能被覆盖；升级时请两端一起更新。
- **提醒在电脑端**：手机端只展示「已到点/已提醒」状态，浏览器页面关闭后不会弹系统通知。

## 许可证

建议采用 **MIT License**。仓库根目录可自行添加 `LICENSE` 文件（版权人写你自己），本文档不预置许可证正文。若二次分发，请保留 `reference/` 中 v1.0 原始文件的来源说明。
