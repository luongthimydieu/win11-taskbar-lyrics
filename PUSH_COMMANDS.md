# 推送到 GitHub 的命令（Git Bash）

> 适用仓库：`G:\deepseek harness\win11-taskbar-lyrics\github-pages`（手机端部署包，已初始化本地 Git 并提交好）。
> 本文只给命令，不包含任何凭据；Token 由你本人生成、只保存在你自己的 Git 凭据/系统里。

## 0. 前置准备

1. 在 github.com 新建一个**空仓库**（不要勾 “Add a README file”“.gitignore”“license”，否则第一次 push 要先 rebase）；
2. 生成一个 **push 用的 Token**：GitHub → 右上头像 → **Settings** → **Developer settings** → **Personal access tokens** → **Tokens (classic)** → **Generate new token (classic)** → 勾选 **`repo`** → 生成并复制。
   - 注意：手机端 App 里填的是**只有 `gist` 权限**的另一个 Token，两者不要混用；
   - 如果图省事，也可以建一个同时勾 `repo` + `gist` 的 classic token 两边共用（不推荐，权限更大）。

> **本次目标仓库（已确认：仓库存在且为空，可直接 push）**
>
> - 仓库：`https://github.com/luongthimydieu/taskbar-todo`
> - Pages 地址：`https://luongthimydieu.github.io/taskbar-todo/`
> - 本机的 `github-pages` 目录已经加好 `origin`，你只需要执行 `git push -u origin main`（会提示输入用户名 + Token）。

## 1. 推送到 GitHub Pages 仓库

```bash
# 1) 进入要推送的目录（Git Bash 的路径写法，注意引号不能少）
cd "/g/deepseek harness/win11-taskbar-lyrics/github-pages"

# 2) 确认本地仓库状态（可选，当前应有 2 个提交、工作区干净、无远程）
git log --oneline
git status

# 3) 设置提交身份（只有以后你自己提交新改动时才需要；不提交可跳过）
git config --global user.name "luongthimydieu"
git config --global user.email "luongthimydieu@users.noreply.github.com"

# 4) 关联远程仓库（已按你的仓库填好；若提示 origin 已存在则跳过）
git remote add origin https://github.com/luongthimydieu/taskbar-todo.git
git remote -v      # 确认 origin 指向正确

# 5) 推送 main 分支
git push -u origin main
```

**认证提示**：Git Bash 弹出 `Username for 'https://github.com'` 时填 GitHub 用户名；
弹出 `Password for 'https://...'` 时**粘贴第 0 步生成的 PAT**（不是 GitHub 登录密码）。
GitHub 早已不支持密码推送，粘贴 Token 即可。

## 2. 开启 GitHub Pages

推成功后，在仓库页面：**Settings → Pages → Source: Deploy from a branch → Branch: main → Folder: /(root) → Save**。
等 1–2 分钟，访问：

```text
https://luongthimydieu.github.io/taskbar-todo/
```

手机浏览器打开这个地址 → 右上角「未配置」→ 填 `gist` Token →「创建新 Gist」→「保存并同步」。
（也可以用手机浏览器自带的“添加到主屏幕”，之后像 App 一样使用。）

## 3. 以后更新部署（改完代码再推送）

```bash
cd "/g/deepseek harness/win11-taskbar-lyrics/github-pages"
git add -A
git commit -m "更新手机端页面"
git push
```

> 如果没配全局身份，`git commit` 会要求先 `git config user.name/user.email`。

## 4. 常见报错处理

| 报错 | 原因 | 处理 |
| --- | --- | --- |
| `remote origin already exists` | 之前已加过 remote | `git remote set-url origin https://github.com/luongthimydieu/taskbar-todo.git` |
| `Updates were rejected ... non-fast-forward` | 远程仓库初始化时自带了 README/提交 | `git pull --rebase origin main` 后 `git push -u origin main` |
| `fatal: refusing to merge unrelated histories` | 两边历史没有共同祖先 | `git pull --rebase origin main --allow-unrelated-histories` 再 push；或确认远程是空仓库后 `git push -u origin main --force` |
| `Support for password authentication was removed` | 把登录密码当凭证用了 | 改用第 0 步的 PAT |
| `Permission denied (publickey)` | 用的是 SSH 远程地址但没有可用 SSH key | 换回 `https://...` 地址，或先 `ssh-keygen` 并把公钥加到 GitHub |
| `Your branch is behind 'origin/main'` | 远程在别处更新过 | `git pull --rebase` 后 push |

## 5. 可选：把已有两个提交的作者改成你自己

```bash
cd "/g/deepseek harness/win11-taskbar-lyrics/github-pages"
git config user.name "你的名字"
git config user.email "你的GitHub邮箱"
git rebase --root --exec "git commit --amend --reset-author --no-edit"
```

## 6. 推送“完整项目”仓库（桌面端 + 文档 + tests + 手机端页面源码）

> 手机端部署仓库（`taskbar-todo`）已经推送成功（远程 `main` = 本地 `3e77807`）。
> 完整项目源码建议再建一个**私有仓库**；`github-pages/` 作为普通文件收录，同时它自己仍是独立的 Pages 仓库（两边互不影响）。

**第 1 步：在 github.com 新建一个空仓库**（不要勾 README / .gitignore / license）：

- Repository name：`win11-taskbar-lyrics`
- Visibility：**Private**（推荐）
- 直接 Create。

**第 2 步：推送（本机仓库已初始化并提交好，只差远程推送）**

```bash
cd "/g/deepseek harness/win11-taskbar-lyrics"

# 确认远程（默认已配好；仓库名改了就用 set-url 改一下）
git remote -v
git remote set-url origin https://github.com/luongthimydieu/win11-taskbar-lyrics.git

# 推送 main
git push -u origin main
```

**认证**：用户名 `luongthimydieu`，密码用 classic PAT（勾 `repo` 权限，和第 0 步相同）。

**第 3 步（可选）：给仓库加资料**

- About 描述：`Win11 任务栏歌词 + 网易云精选热评 + 跨端待办（GitHub Gist 同步）`
- Topics：`windows11` `pyqt6` `taskbar` `lyrics` `netease` `pwa` `github-pages` `gist`

> 注意：`desktop/dist/`、`desktop/build/`、`__pycache__/` 已被 `.gitignore` 排除，不会把 exe / 缓存传上去；要发 exe 用 GitHub Release 上传。

推完后两个仓库分开维护：`github-pages` 仓库只放静态页面用于 Pages；`win11-taskbar-lyrics` 仓库放完整源码与文档。

## 7. 其他说明

- 想让 Git 记住 Token（Windows 凭据管理器）：`git config --global credential.helper manager`（Git for Windows 自带 Git Credential Manager 时可用）；
- 本目录 `.gitignore` 已排除 `dist/`、`build/`、`__pycache__` 等，不会把打出来的 exe 或缓存推上去；
- `github-pages` 里没有任何密钥或 Token（已检查，`index.html` 只有 `ghp_xxxx` 占位符），公开仓库安全。
