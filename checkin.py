#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GLaDOS 自动签到脚本（积分 + 剩余天数 完整显示版）
- 正确获取积分（/api/user/points）
- 正确获取会员剩余天数（/api/user/status 的 leftDays）
- 支持多账号
- PushPlus 推送
"""

import os
import sys
import time
import json
from datetime import datetime
import requests

# ================= 配置 =================
DOMAINS = [
    "https://glados.cloud",
    "https://glados-facility.com",
    "https://glados.space",
    "https://glados.rocks",
    "https://glados.network",
]

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

HEADERS_BASE = {
    "Content-Type": "application/json;charset=UTF-8",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}")


def get_cookies() -> list:
    raw = os.environ.get("GLADOS_COOKIE", "").strip()
    if not raw:
        log("❌ 未配置环境变量 GLADOS_COOKIE")
        return []
    # 支持 & 或换行分隔多账号
    if "\n" in raw:
        items = raw.split("\n")
    else:
        items = raw.split("&")
    cookies = []
    for item in items:
        c = item.strip()
        if c:
            # 去掉可能误复制的 Cookie: 前缀
            if c.lower().startswith("cookie:"):
                c = c[7:].strip()
            cookies.append(c)
    return cookies


class GLaDOS:
    def __init__(self, cookie: str):
        self.cookie = cookie
        self.domain = DOMAINS[0]
        self.email = "?"
        self.left_days = "?"
        self.points = "?"
        self.points_change = ""

    def _request(self, method: str, path: str, data=None):
        for domain in DOMAINS:
            try:
                url = f"{domain}{path}"
                headers = HEADERS_BASE.copy()
                headers["User-Agent"] = os.environ.get("GLADOS_USER_AGENT", DEFAULT_UA)
                headers["Cookie"] = self.cookie
                headers["Origin"] = domain
                headers["Referer"] = f"{domain}/console/checkin"

                if method.upper() == "GET":
                    resp = requests.get(url, headers=headers, timeout=12)
                else:
                    resp = requests.post(url, headers=headers, json=data, timeout=12)

                if resp.status_code == 200:
                    self.domain = domain
                    return resp.json()
                else:
                    log(f"⚠️ {domain} 返回 HTTP {resp.status_code}")
            except Exception as e:
                log(f"⚠️ {domain} 请求失败: {e}")
                continue
        return None

    def get_status(self) -> bool:
        """获取邮箱和会员剩余天数"""
        res = self._request("GET", "/api/user/status")
        if res and res.get("code") == 0 and "data" in res:
            d = res["data"]
            self.email = d.get("email", "Unknown")
            left = str(d.get("leftDays", "?")).split(".")[0]
            self.left_days = left
            return True
        return False

    def get_points(self) -> bool:
        """获取当前积分（关键：积分在独立接口）"""
        res = self._request("GET", "/api/user/points")
        if not res:
            return False

        # 兼容两种返回结构
        points = res.get("points")
        if points is None and isinstance(res.get("data"), dict):
            points = res["data"].get("points")

        if points is not None:
            self.points = str(points).split(".")[0]

            # 尝试获取最近一次积分变化
            history = res.get("history") or (res.get("data") or {}).get("history") or []
            if history and isinstance(history, list) and len(history) > 0:
                change = str(history[0].get("change", "0")).split(".")[0]
                if change and not change.startswith("-") and change != "0":
                    change = "+" + change
                self.points_change = change
            return True
        return False

    def checkin(self):
        """执行签到"""
        return self._request("POST", "/api/user/checkin", {"token": "glados.cloud"})


def pushplus(token: str, title: str, content: str):
    if not token:
        return
    try:
        url = "http://www.pushplus.plus/send"
        requests.get(
            url,
            params={
                "token": token,
                "title": title,
                "content": content,
                "template": "html",
            },
            timeout=8,
        )
        log("✅ PushPlus 推送成功")
    except Exception as e:
        log(f"❌ PushPlus 推送失败: {e}")


def main():
    cookies = get_cookies()
    if not cookies:
        sys.exit(1)

    log(f"🚀 GLaDOS 签到开始，共 {len(cookies)} 个账号")
    results = []
    success_count = 0

    for idx, cookie in enumerate(cookies, 1):
        g = GLaDOS(cookie)

        # 1. 先获取状态（邮箱 + 剩余天数）
        if not g.get_status():
            log(f"❌ 账号 {idx} 状态查询失败（Cookie 可能失效）")
            results.append(f"❌ 账号{idx}: 状态查询失败")
            continue

        # 2. 获取积分
        g.get_points()

        # 3. 签到
        checkin_res = g.checkin()
        if not checkin_res:
            msg = "签到请求失败"
            log(f"❌ 用户: {g.email} | 积分: {g.points} | 天数: {g.left_days} | 结果: {msg}")
            results.append(f"❌ {g.email}<br>积分: {g.points} | 天数: {g.left_days}<br>结果: {msg}")
            continue

        msg = checkin_res.get("message", "未知结果")
        # 判断是否成功（首次签到 or 今日已签到都算正常）
        msg_lower = msg.lower()
        if any(k in msg_lower for k in ["checkin", "observation logged", "got", "repeats"]):
            success_count += 1
            status_icon = "✅"
        else:
            status_icon = "⚠️"

        change_str = f" ({g.points_change})" if g.points_change else ""
        log(f"{status_icon} 用户: {g.email} | 积分: {g.points}{change_str} | 天数: {g.left_days} | 结果: {msg}")

        results.append(
            f"{status_icon} <b>{g.email}</b><br>"
            f"当前积分: <b>{g.points}</b>{change_str}<br>"
            f"剩余天数: <b>{g.left_days}</b> 天<br>"
            f"签到结果: {msg}"
        )

        # 简单限速，避免请求过快
        if idx < len(cookies):
            time.sleep(1.5)

    # 推送汇总
    ptoken = os.environ.get("PUSHPLUS_TOKEN", "").strip()
    if ptoken and results:
        title = f"GLaDOS 签到 {success_count}/{len(cookies)}"
        content = "<br><br>".join(results)
        pushplus(ptoken, title, content)

    log(f"🏁 完成，成功 {success_count}/{len(cookies)}")
    # 只要有一个成功就返回 0
    sys.exit(0 if success_count > 0 else 1)


if __name__ == "__main__":
    main()