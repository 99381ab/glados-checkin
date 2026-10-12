#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GLaDOS 自动签到脚本（积分 + 剩余天数 + QQ 邮件通知版）

功能：
1. 支持多账号签到
2. 自动切换备用域名
3. 获取用户邮箱和会员剩余天数
4. 获取当前积分和最近积分变化
5. 自动执行签到
6. 支持 PushPlus 推送
7. 至少一个账号签到成功时，发送 QQ 邮件通知

GitHub Secrets / 环境变量：
GLADOS_COOKIE
GLADOS_USER_AGENT（可选）
PUSHPLUS_TOKEN（可选）
QQ_SMTP_USER（可选）
QQ_SMTP_AUTH_CODE（可选）
QQ_MAIL_TO（可选）
"""

import os
import sys
import time
import smtplib
from datetime import datetime
from email.mime.text import MIMEText
from email.header import Header

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


# ================= 日志 =================

def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}")


# ================= 获取多账号 Cookie =================

def get_cookies() -> list:
    raw = os.environ.get("GLADOS_COOKIE", "").strip()

    if not raw:
        log("❌ 未配置环境变量 GLADOS_COOKIE")
        return []

    # 支持换行或 & 分隔多个账号
    if "\n" in raw:
        items = raw.split("\n")
    else:
        items = raw.split("&")

    cookies = []

    for item in items:
        cookie = item.strip()

        if not cookie:
            continue

        # 去掉可能误复制的 Cookie: 前缀
        if cookie.lower().startswith("cookie:"):
            cookie = cookie[7:].strip()

        cookies.append(cookie)

    return cookies


# ================= GLaDOS 客户端 =================

class GLaDOS:

    def __init__(self, cookie: str):
        self.cookie = cookie
        self.domain = DOMAINS[0]
        self.email = "?"
        self.left_days = "?"
        self.points = "?"
        self.points_change = ""

    def _request(self, method: str, path: str, data=None):
        """
        请求 GLaDOS API。
        请求失败时尝试其他域名。
        """

        for domain in DOMAINS:
            try:
                url = f"{domain}{path}"

                headers = HEADERS_BASE.copy()
                headers["User-Agent"] = os.environ.get(
                    "GLADOS_USER_AGENT", DEFAULT_UA
                )
                headers["Cookie"] = self.cookie
                headers["Origin"] = domain
                headers["Referer"] = f"{domain}/console/checkin"

                if method.upper() == "GET":
                    resp = requests.get(
                        url,
                        headers=headers,
                        timeout=12
                    )
                else:
                    resp = requests.post(
                        url,
                        headers=headers,
                        json=data,
                        timeout=12
                    )

                if resp.status_code == 200:
                    try:
                        result = resp.json()
                    except ValueError:
                        log(f"⚠️ {domain} 返回的不是有效 JSON")
                        continue

                    self.domain = domain
                    return result

                log(f"⚠️ {domain} 返回 HTTP {resp.status_code}")

            except requests.RequestException as e:
                log(f"⚠️ {domain} 请求失败: {e}")
                continue

        return None

    def get_status(self) -> bool:
        """获取用户邮箱和会员剩余天数"""

        res = self._request("GET", "/api/user/status")

        if (
            isinstance(res, dict)
            and res.get("code") == 0
            and isinstance(res.get("data"), dict)
        ):
            data = res["data"]

            self.email = data.get("email", "Unknown")

            left = data.get("leftDays", "?")

            if left is not None:
                self.left_days = str(left).split(".")[0]

            return True

        return False

    def get_points(self) -> bool:
        """获取当前积分和最近一次积分变化"""

        res = self._request("GET", "/api/user/points")

        if not isinstance(res, dict):
            return False

        points = res.get("points")

        data = res.get("data")

        if points is None and isinstance(data, dict):
            points = data.get("points")

        if points is None:
            return False

        self.points = str(points).split(".")[0]

        history = res.get("history")

        if history is None and isinstance(data, dict):
            history = data.get("history")

        if isinstance(history, list) and history:
            first = history[0]

            if isinstance(first, dict):
                change = str(first.get("change", "0")).split(".")[0]

                if change and not change.startswith("-") and change != "0":
                    change = "+" + change

                self.points_change = change

        return True

    def checkin(self):
        """执行签到"""

        return self._request(
            "POST",
            "/api/user/checkin",
            {"token": "glados.cloud"}
        )


# ================= PushPlus 推送 =================

def pushplus(token: str, title: str, content: str):
    """发送 PushPlus 通知"""

    if not token:
        return

    try:
        url = "https://www.pushplus.plus/send"

        resp = requests.get(
            url,
            params={
                "token": token,
                "title": title,
                "content": content,
                "template": "html",
            },
            timeout=8,
        )

        if resp.status_code == 200:
            log("✅ PushPlus 请求已发送")
        else:
            log(f"⚠️ PushPlus 返回 HTTP {resp.status_code}")

    except requests.RequestException as e:
        log(f"❌ PushPlus 推送失败: {e}")


# ================= QQ 邮件通知 =================

def send_qq_mail(subject: str, content: str) -> bool:
    """
    通过 QQ 邮箱 SMTP 服务发送通知。

    所需环境变量：
    QQ_SMTP_USER
    QQ_SMTP_AUTH_CODE
    QQ_MAIL_TO
    """

    smtp_user = os.environ.get("QQ_SMTP_USER", "").strip()
    smtp_auth_code = os.environ.get("QQ_SMTP_AUTH_CODE", "").strip()
    mail_to = os.environ.get("QQ_MAIL_TO", "").strip()

    if not smtp_user or not smtp_auth_code or not mail_to:
        log("⚠️ QQ 邮件配置不完整，跳过邮件通知")
        return False

    message = MIMEText(content, "plain", "utf-8")
    message["Subject"] = Header(subject, "utf-8")
    message["From"] = smtp_user
    message["To"] = mail_to

    try:
        with smtplib.SMTP_SSL(
            "smtp.qq.com",
            465,
            timeout=15
        ) as server:

            server.login(smtp_user, smtp_auth_code)

            server.sendmail(
                smtp_user,
                [mail_to],
                message.as_string()
            )

        log("✅ QQ 邮件发送成功")
        return True

    except Exception as e:
        # 不输出授权码或 Cookie
        log(f"❌ QQ 邮件发送失败: {type(e).__name__}: {e}")
        return False


# ================= 判断签到结果 =================

def is_checkin_success(checkin_res) -> bool:
    """
    根据接口返回的 message 判断签到结果。

    注意：
    此处沿用原脚本的关键词判断方式。
    如果 GLaDOS API 返回格式发生变化，需要相应调整。
    """

    if not isinstance(checkin_res, dict):
        return False

    msg = checkin_res.get("message", "")

    if not isinstance(msg, str):
        return False

    msg_lower = msg.lower()

    success_keywords = [
        "checkin",
        "observation logged",
        "got",
        "repeats",
    ]

    return any(keyword in msg_lower for keyword in success_keywords)


# ================= 主程序 =================

def main():

    cookies = get_cookies()

    if not cookies:
        sys.exit(1)

    log(f"🚀 GLaDOS 签到开始，共 {len(cookies)} 个账号")

    results = []
    success_count = 0

    for idx, cookie in enumerate(cookies, 1):

        g = GLaDOS(cookie)

        # 1. 查询账号状态
        if not g.get_status():
            log(f"❌ 账号 {idx} 状态查询失败（Cookie 可能失效）")

            results.append(
                f"❌ 账号 {idx}\n"
                f"状态查询失败，Cookie 可能失效"
            )

            if idx < len(cookies):
                time.sleep(1.5)

            continue

        # 2. 查询积分
        g.get_points()

        # 3. 执行签到
        checkin_res = g.checkin()

        if not isinstance(checkin_res, dict):
            msg = "签到请求失败"

            log(
                f"❌ 用户: {g.email} | "
                f"积分: {g.points} | "
                f"天数: {g.left_days} | "
                f"结果: {msg}"
            )

            results.append(
                f"❌ {g.email}\n"
                f"当前积分: {g.points}\n"
                f"剩余天数: {g.left_days} 天\n"
                f"签到结果: {msg}"
            )

        else:
            msg = checkin_res.get("message", "未知结果")

            if not isinstance(msg, str):
                msg = str(msg)

            success = is_checkin_success(checkin_res)

            if success:
                success_count += 1
                status_icon = "✅"
                status_text = "成功"
            else:
                status_icon = "⚠️"
                status_text = "未确认成功"

            change_str = (
                f" ({g.points_change})"
                if g.points_change
                else ""
            )

            log(
                f"{status_icon} 用户: {g.email} | "
                f"积分: {g.points}{change_str} | "
                f"天数: {g.left_days} | "
                f"结果: {msg}"
            )

            results.append(
                f"{status_icon} {g.email}\n"
                f"签到状态: {status_text}\n"
                f"当前积分: {g.points}{change_str}\n"
                f"剩余天数: {g.left_days} 天\n"
                f"签到结果: {msg}"
            )

        # 限速，避免请求过快
        if idx < len(cookies):
            time.sleep(1.5)

    # 4. PushPlus 推送汇总
    ptoken = os.environ.get("PUSHPLUS_TOKEN", "").strip()

    if ptoken and results:
        title = f"GLaDOS 签到 {success_count}/{len(cookies)}"

        content = "<br><br>".join(
            result.replace("\n", "<br>")
            for result in results
        )

        pushplus(ptoken, title, content)

    # 5. QQ 邮件通知
    # 只有至少一个账号被判定为签到成功时才发送
    if success_count > 0:

        mail_content = (
            "GLaDOS 自动签到通知\n"
            "==============================\n\n"
            f"签到时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"账号总数：{len(cookies)}\n"
            f"成功账号数：{success_count}\n\n"
            "各账号签到详情：\n\n"
            + "\n\n------------------------------\n\n".join(results)
        )

        send_qq_mail(
            subject=f"GLaDOS 签到通知：成功 {success_count}/{len(cookies)}",
            content=mail_content
        )

    else:
        log("ℹ️ 没有确认签到成功的账号，本次不发送 QQ 邮件")

    # 6. 完成
    log(f"🏁 完成，成功 {success_count}/{len(cookies)}")

    # 只要有一个账号被判定成功，就返回 0
    sys.exit(0 if success_count > 0 else 1)


if __name__ == "__main__":
    main()