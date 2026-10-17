# GitHub Pages 手机端（部署包）

这个目录就是**要推到 GitHub 上的那部分代码**：一个纯静态、零后端的手机端单页应用。

```
github-pages/
├── index.html            # 单页应用（内含同步协议 v2 的完整实现）
├── manifest.webmanifest  # PWA 清单（可添加到手机主屏幕）
├── sw.js                 # Service Worker（应用外壳离线缓存）
├── icon-192.png          # 主屏幕图标
├── icon-512.png          # 主屏幕图标（含 maskable）
├── apple-touch-icon.png  # iOS 主屏幕图标
├── .nojekyll             # 跳过 Jekyll 处理（保证下划线文件按原样发布）
└── README.md             # 本文件
```

## 一、推送到 GitHub 并开启 Pages

本目录已经初始化过本地 Git 仓库并完成一次提交（提交哈希可用 `git log` 查看），
你只需要接上远程仓库然后推送：

```bash
cd github-pages          # 如果还没进入这个目录
git remote add origin https://github.com/<你的用户名>/<仓库名>.git
git remote -v            # 确认远程地址
git push -u origin main
```

> 如果希望这次提交的作者是你自己，可以先配置 Git 身份再修正作者：
>
> ```bash
> git config user.name "<你的名字>"
> git config user.email "<你的邮箱>"
> git commit --amend --reset-author --no-edit
> ```
>
> 如果不想用现成的本地仓库：删掉 `github-pages/.git` 目录，再按传统流程
> `git init` → `git add .` → `git commit -m "init"` → `git branch -M main` 重新来一遍。

然后在 GitHub 仓库页面操作：

1. **Settings → Pages**
2. Source 选 **Deploy from a branch**
3. Branch 选 **main**，目录选 **/(root)**，保存
4. 等 1–2 分钟，页面顶部会出现站点地址：`https://<你的用户名>.github.io/<仓库名>/`

> 说明：GitHub Pages 对**公开仓库**免费；私密仓库的 Pages 需要付费套餐。
> 这个目录里没有任何密钥 —— Token 只存在手机浏览器的 localStorage 里，
> 所以仓库公开是安全的。

## 二、手机端首次配置

1. 手机浏览器打开上面生成的 Pages 地址；
2. 点右上角「未配置」展开配置面板，填入 GitHub Token（classic，勾选 `gist` 权限）；
3. 点「创建新 Gist」→ 页面自动填好 Gist ID（同时创建一个私密 Gist 当云数据库）；
4. 「保存并同步」；
5. 想当 App 用：Safari → 分享 → *添加到主屏幕*；Chrome → 菜单 → *安装应用 / 添加到主屏幕*。

## 三、和电脑端对接

1. 电脑端右键任务栏胶囊 →「⚙️ 配置 GitHub Gist 云同步」；
2. 填入**同一个** Token 和 Gist ID → 保存；
3. 电脑端每 30 秒同步一次；在任务栏上点完成待办会回写云端，手机端回到前台会自动刷新。

## 四、更新部署

改完文件后：

```bash
git add .
git commit -m "update"
git push
```

GitHub Pages 会自动重新发布；手机端刷新页面即可（Service Worker 采用“网络优先”，
联网时总是拿到最新外壳）。

## 五、常见问题

- **打开页面白屏/报错**：先确认仓库 Pages 已发布完成，再强制刷新；
- **手机端能用但电脑端不同步**：检查两端 Token、Gist ID 是否完全一致；
- **Token 填错后一直 401**：重新生成 Token（只勾 `gist`），在配置面板覆盖保存；
- **删除待办后旧数据又出现**：这是同步机制在合并，多等一轮（30 秒）即可；
  协议用墓碑记录删除，正常不会复活。
