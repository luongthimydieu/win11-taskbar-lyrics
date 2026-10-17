#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""publish_release.py — 用 GitHub API 把打包好的 zip 发布成 GitHub Release。

用法（Git Bash / PowerShell / CMD 均可）：

    set GH_TOKEN=ghp_xxx          # CMD；PowerShell 用 $env:GH_TOKEN="ghp_xxx"
    python tools/publish_release.py --tag v1.2.2 \
        --zip desktop/dist/Win11TaskbarLyrics-v1.2.2-onedir.zip \
        --notes RELEASE_NOTES_v1.2.2.md \
        --title "v1.2.2 — Win11 任务栏歌词 · 跨端待办"

Token 需要 classic PAT（repo 权限）或 fine-grained token（Contents: write）。
本脚本不会把 Token 写进任何文件/日志。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from urllib.parse import quote

import requests

DEFAULT_REPO = "luongthimydieu/win11-taskbar-lyrics"


def api(method: str, url: str, token: str, **kwargs):
    headers = {
        "Authorization": "token " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    headers.update(kwargs.pop("headers", {}))
    response = requests.request(method, url, headers=headers, timeout=kwargs.pop("timeout", 60), **kwargs)
    return response


def upload_asset(repo: str, release_id: int, token: str, path: Path, content_type: str) -> None:
    url = (
        f"https://uploads.github.com/repos/{repo}/releases/{release_id}/assets"
        f"?name={quote(path.name)}"
    )
    print(f"上传 {path.name}（{path.stat().st_size / 1024 / 1024:.1f} MB）…", flush=True)
    with path.open("rb") as handle:
        response = api(
            "POST",
            url,
            token,
            headers={"Content-Type": content_type},
            data=handle,
            timeout=1800,
        )
    if response.status_code not in (200, 201):
        raise SystemExit(f"上传失败：HTTP {response.status_code} {response.text[:300]}")
    data = response.json()
    print(f"  完成：{data.get('browser_download_url')}")


def main() -> None:
    parser = argparse.ArgumentParser(description="发布 GitHub Release")
    parser.add_argument("--repo", default=DEFAULT_REPO, help=f"owner/repo，默认 {DEFAULT_REPO}")
    parser.add_argument("--tag", required=True, help="例如 v1.2.2（本地 tag 最好已推送）")
    parser.add_argument("--zip", type=Path, required=True, help="要上传的 zip")
    parser.add_argument("--notes", type=Path, default=None, help="Release 说明 Markdown 文件")
    parser.add_argument("--title", default=None, help="Release 标题，默认用 tag")
    parser.add_argument("--target", default="main", help="目标分支，默认 main")
    args = parser.parse_args()

    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        raise SystemExit("缺少 GH_TOKEN 环境变量（classic PAT，勾 repo 权限）")
    if not args.zip.exists():
        raise SystemExit(f"找不到要上传的文件：{args.zip}")

    body = args.notes.read_text(encoding="utf-8") if args.notes and args.notes.exists() else ""
    payload = {
        "tag_name": args.tag,
        "target_commitish": args.target,
        "name": args.title or args.tag,
        "body": body,
        "draft": False,
        "prerelease": False,
    }
    print(f"创建 Release：{args.repo} {args.tag}", flush=True)
    response = api("POST", f"https://api.github.com/repos/{args.repo}/releases", token, json=payload)
    if response.status_code == 422 and "already_exists" in response.text:
        # Release 已存在：查找后直接补传附件
        listing = api(
            "GET", f"https://api.github.com/repos/{args.repo}/releases/tags/{args.tag}", token
        )
        listing.raise_for_status()
        release = listing.json()
        print("Release 已存在，改为补传附件")
    elif response.status_code not in (200, 201):
        raise SystemExit(f"创建 Release 失败：HTTP {response.status_code} {response.text[:300]}")
    else:
        release = response.json()
        print(f"Release 已创建：{release.get('html_url')}")

    release_id = int(release["id"])
    existing = {asset.get("name") for asset in release.get("assets", [])}
    if args.zip.name not in existing:
        upload_asset(args.repo, release_id, token, args.zip, "application/zip")
    else:
        print(f"附件已存在，跳过：{args.zip.name}")

    sha_file = args.zip.with_name("SHA256SUMS.txt")
    if sha_file.exists() and sha_file.name not in existing:
        upload_asset(args.repo, release_id, token, sha_file, "text/plain")

    print("发布完成 ✅", flush=True)
    print(f"下载页：https://github.com/{args.repo}/releases/tag/{args.tag}")


if __name__ == "__main__":
    sys.exit(main())
