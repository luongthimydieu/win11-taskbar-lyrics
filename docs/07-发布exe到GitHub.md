# 07 发布 exe 到 GitHub Release

> ✅ **最新 v2.0.0 已发布（2026-10-20）**
>
> - Release 页面：<https://github.com/luongthimydieu/win11-taskbar-lyrics/releases/tag/v2.0.0>
> - 直接下载：<https://github.com/luongthimydieu/win11-taskbar-lyrics/releases/download/v2.0.0/Win11TaskbarLyrics-v2.0.0-onedir.zip>
> - SHA256：`02448B6A5F33A9C8D8EF534D29040A30E5DB2D69D91D850985E95452EF6EFD06`
>
> 历史版本：v1.3.8、v1.3.7、v1.3.5、v1.3.4、v1.3.3、v1.3.2、v1.3.1、v1.3.0 及更早版本仍保留在 Releases 页面。
>
> 注意：`win11-taskbar-lyrics` 目前是**公开仓库**（Releases 可下载）；如果不想公开，可在 Settings 改回 Private。

本文把 `desktop/dist/Win11TaskbarLyrics-v1.2.9-onedir.zip` 发布到 GitHub 的 Releases 页面，让别人可以直接下载使用。

## 0. 先确认两件事

1. **exe 在哪里**：
   - 打包产物：`G:\deepseek harness\win11-taskbar-lyrics\desktop\dist\Win11TaskbarLyrics\`（`Win11TaskbarLyrics.exe` + `_internal\`，约 141 MB）
   - 本机安装/运行目录：`%LOCALAPPDATA%\Programs\Win11TaskbarLyrics\`（当前正在运行，开机自启也指向这里）
   - 发布用压缩包：`desktop\dist\Win11TaskbarLyrics-v1.2.2-onedir.zip`
2. **仓库**：完整项目仓库 `luongthimydieu/win11-taskbar-lyrics`（私有/公开都可以）已经推送；`desktop/dist/` 被 `.gitignore` 排除，所以 zip **不在 git 里**，要作为 Release 附件上传。

> 私有仓库的 Release 只有自己/协作者能下载；想让别人下载就把仓库设为 Public，或把附件发文件给对方。

## 1. 方式 A：网页操作（最简单，推荐）

1. 打开 `https://github.com/luongthimydieu/win11-taskbar-lyrics/releases`；
2. 点 **Draft a new release**；
3. **Choose a tag** 输入 `v1.2.2` → 点 **Create new tag: v1.2.2 on publish**；
4. Target 选 `main`；
5. Release title：`v1.2.2 — Win11 任务栏歌词 · 跨端待办`；
6. Describe：把仓库根目录 [`RELEASE_NOTES_v1.2.2.md`](../RELEASE_NOTES_v1.2.2.md) 的内容粘贴进去；
7. **Attach binaries**：上传 `desktop\dist\Win11TaskbarLyrics-v1.2.2-onedir.zip`（141 MB 原始目录压缩后约 60–70 MB）；
8. 点 **Publish release**；
9. 发布后把下载地址发给别人：
   `https://github.com/luongthimydieu/win11-taskbar-lyrics/releases/tag/v1.2.2`

## 2. 方式 B：GitHub CLI（一条命令）

先安装并登录（只需一次）：

```powershell
winget install --id GitHub.cli -e
gh auth login          # 选 GitHub.com → HTTPS → 浏览器登录
```

然后在项目根目录执行：

```powershell
cd "G:\deepseek harness\win11-taskbar-lyrics"
gh release create v1.2.2 "desktop\dist\Win11TaskbarLyrics-v1.2.2-onedir.zip" `
  --title "v1.2.2 — Win11 任务栏歌词 · 跨端待办" `
  --notes-file RELEASE_NOTES_v1.2.2.md `
  --target main
```

后续想追加/替换文件：

```powershell
gh release upload v1.2.2 "desktop\dist\Win11TaskbarLyrics-v1.2.2-onedir.zip" --clobber
```

## 3. 方式 C：git 标签 + 网页上传

```powershell
cd "G:\deepseek harness\win11-taskbar-lyrics"
git tag -a v1.2.2 -m "v1.2.2 任务栏歌词 + 每日待办节奏"
git push origin v1.2.2
```

推送成功后打开 `https://github.com/luongthimydieu/win11-taskbar-lyrics/releases/tag/v1.2.2`，点 **Edit** → 上传 zip → 填写说明 → Publish。

## 4. 发布前验证

```powershell
# 1) 解压 zip 到临时目录，跑自检（退出码应为 0）
Expand-Archive "desktop\dist\Win11TaskbarLyrics-v1.2.2-onedir.zip" "$env:TEMP\tbl-release-test" -Force
$exe = "$env:TEMP\tbl-release-test\Win11TaskbarLyrics.exe"
Start-Process $exe -ArgumentList "--selftest" -Wait -PassThru | Select-Object ExitCode

# 2) 计算 SHA256（写进 Release 说明里，方便别人校验）
Get-FileHash "desktop\dist\Win11TaskbarLyrics-v1.2.2-onedir.zip" -Algorithm SHA256
```

## 5. 以后每个版本怎么发

```powershell
# 1) 改版本号（taskbar_widget.py 里的 APP_VERSION）并提交
# 2) 重新打包
powershell -ExecutionPolicy Bypass -File desktop\build_exe.ps1
# 3) 生成新 zip（注意改版本号）
Compress-Archive -Path "desktop\dist\Win11TaskbarLyrics\*" `
  -DestinationPath "desktop\dist\Win11TaskbarLyrics-v1.2.3-onedir.zip" -CompressionLevel Optimal
# 4) 发布（gh 方式）
gh release create v1.2.3 "desktop\dist\Win11TaskbarLyrics-v1.2.3-onedir.zip" --title "v1.2.3" --notes-file RELEASE_NOTES_v1.2.3.md
```

> 不要把 `dist/` 提交进 git；Release 附件才是正确载体。压缩包 141 MB 在 GitHub Release 单文件 2 GB 限制内，没有问题。
