#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
GLaDOS 自动签到脚本

功能：
1. 多账号签到
2. 自动切换备用域名
3. 查询会员剩余天数
4. 查询签到前后积分
5. 显示本次实际签到奖励
6. 只有本次获得签到积分时才发送 QQ 邮件
7. 支持 PushPlus
8. 日志和邮件时间统一使用北京时间

环境变量：
GLADOS_COOKIE
GLADOS_USER_AGENT（可选）
PUSHPLUS_TOKEN（可选）
QQ_SMTP_USER（可选）
QQ_SMTP_AUTH_CODE（可选）
QQ_MAIL_TO（可选）
"""

import os
import sys
import re
import time
import smtplib

from datetime import datetime
from zoneinfo import ZoneInfo
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

BEIJING_TZ = ZoneInfo("Asia/Shanghai")


def now_str():
    return datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")


def log(msg: str):
    print(f"[{now_str()}] {msg}")


# ================= Cookie =================

def get_cookies():
    raw = os.environ.get("GLADOS_COOKIE", "").strip()

    if not raw:
        log("❌ 未配置 GLADOS_COOKIE")
        return []

    if "\n" in raw:
        items = raw.splitlines()
    else:
        items = raw.split("&")

    cookies = []

    for item in items:
        cookie = item.strip()

        if not cookie:
            continue

        if cookie.lower().startswith("cookie:"):
            cookie = cookie[7:].strip()

        cookies.append(cookie)

    return cookies


# ================= GLaDOS 客户端 =================

class GLaDOS:

    def __init__(self, cookie):
        self.cookie = cookie
        self.domain = DOMAINS[0]

        self.email = "Unknown"
        self.left_days = "?"
        self.points_before = None
        self.points_after = None
        self.reward_points = None

        self.session = requests.Session()

    def _request(self, method, path, data=None):

        for domain in DOMAINS:

            try:
                url = f"{domain}{path}"

                headers = HEADERS_BASE.copy()
                headers["User-Agent"] = os.environ.get(
                    "GLADOS_USER_AGENT",
                    DEFAULT_UA
                )
                headers["Cookie"] = self.cookie
                headers["Origin"] = domain
                headers["Referer"] = f"{domain}/console/checkin"

                if method.upper() == "GET":
                    response = self.session.get(
                        url,
                        headers=headers,
                        timeout=12
                    )
                else:
                    response = self.session.post(
                        url,
                        headers=headers,
                        json=data,
                        timeout=12
                    )

                if response.status_code != 200:
                    log(
                        f"⚠️ {domain} 返回 HTTP "
                        f"{response.status_code}"
                    )
                    continue

                try:
                    result = response.json()
                except ValueError:
                    log(f"⚠️ {domain} 返回的不是有效 JSON")
                    continue

                self.domain = domain
                return result

            except requests.RequestException as exc:
                log(
                    f"⚠️ {domain} 请求失败："
                    f"{type(exc).__name__}"
                )

        return None

    def get_status(self):

        result = self._request(
            "GET",
            "/api/user/status"
        )

        if not isinstance(result, dict):
            return False

        if result.get("code") != 0:
            return False

        data = result.get("data")

        if not isinstance(data, dict):
            return False

        self.email = data.get("email", "Unknown")

        left_days = data.get("leftDays")

        if left_days is not None:
            try:
                self.left_days = str(int(float(left_days)))
            except (TypeError, ValueError):
                self.left_days = str(left_days)

        return True

    def get_points(self):
        """
        查询积分。

        兼容两种常见返回结构：
        {"points": 100}
        {"data": {"points": 100}}

        如果实际接口格式不同，需要根据真实响应调整。
        """

        result = self._request(
            "GET",
            "/api/user/points"
        )

        if not isinstance(result, dict):
            return None

        points = result.get("points")

        if points is None:
            data = result.get("data")

            if isinstance(data, dict):
                points = data.get("points")

        if points is None:
            return None

        try:
            return int(float(points))
        except (TypeError, ValueError):
            return None

    def checkin(self):

        return self._request(
            "POST",
            "/api/user/checkin",
            {"token": "glados.cloud"}
        )


# ================= 签到奖励解析 =================

def parse_reward(result):
    """
    从签到接口返回的 message 中提取奖励积分。

    例如：
    Checkin! Got 5 Points -> 5

    没有明确奖励信息则返回 None。
    """

    if not isinstance(result, dict):
        return None

    message = result.get("message", "")

    if not isinstance(message, str):
        return None

    match = re.search(
        r"\bGot\s+(\d+)\s+Points?\b",
        message,
        re.IGNORECASE
    )

    if match:
        return int(match.group(1))

    return None


def is_rewarded(result):
    """
    只有签到结果明确包含正数积分奖励时才认定获得奖励。
    """

    reward = parse_reward(result)

    return reward is not None and reward > 0


# ================= PushPlus =================

def pushplus(token, title, content):

    if not token:
        return

    try:
        response = requests.get(
            "https://www.pushplus.plus/send",
            params={
                "token": token,
                "title": title,
                "content": content,
                "template": "html",
            },
            timeout=10
        )

        if response.status_code == 200:
            log("✅ PushPlus 请求已发送")
        else:
            log(
                f"⚠️ PushPlus 返回 HTTP "
                f"{response.status_code}"
            )

    except requests.RequestException as exc:
        log(f"❌ PushPlus 发送失败：{type(exc).__name__}")


# ================= QQ 邮件 =================

def send_qq_mail(subject, content):

    smtp_user = os.environ.get(
        "QQ_SMTP_USER", ""
    ).strip()

    smtp_auth_code = os.environ.get(
        "QQ_SMTP_AUTH_CODE", ""
    ).strip()

    mail_to = os.environ.get(
        "QQ_MAIL_TO", ""
    ).strip()

    if not smtp_user or not smtp_auth_code or not mail_to:
        log("⚠️ QQ 邮件配置不完整")
        return False

    message = MIMEText(
        content,
        "plain",
        "utf-8"
    )

    message["Subject"] = Header(
        subject,
        "utf-8"
    )

    message["From"] = smtp_user
    message["To"] = mail_to

    try:
        with smtplib.SMTP_SSL(
            "smtp.qq.com",
            465,
            timeout=15
        ) as server:

            server.login(
                smtp_user,
                smtp_auth_code
            )

            server.sendmail(
                smtp_user,
                [mail_to],
                message.as_string()
            )

        log("✅ QQ 邮件发送成功")
        return True

    except Exception as exc:
        log(
            f"❌ QQ 邮件发送失败："
            f"{type(exc).__name__}"
        )
        return False


# ================= 主程序 =================

def main():

    cookies = get_cookies()

    if not cookies:
        sys.exit(1)

    log(
        f"🚀 GLaDOS 签到开始，共 "
        f"{len(cookies)} 个账号"
    )

    results = []
    mail_results = []

    rewarded_count = 0
    failed_count = 0

    for index, cookie in enumerate(cookies, 1):

        g = GLaDOS(cookie)

        # 1. 获取账号状态
        if not g.get_status():

            log(
                f"❌ 账号 {index} 状态查询失败"
            )

            results.append(
                f"❌ 账号 {index}：状态查询失败"
            )

            failed_count += 1

            if index < len(cookies):
                time.sleep(1.5)

            continue

        # 2. 查询签到前积分
        g.points_before = g.get_points()

        # 3. 执行签到
        checkin_result = g.checkin()

        if not isinstance(checkin_result, dict):

            log(
                f"❌ 用户 {g.email} "
                f"签到请求失败"
            )

            results.append(
                f"❌ {g.email}\n"
                f"签到请求失败"
            )

            failed_count += 1

            if index < len(cookies):
                time.sleep(1.5)

            continue

        message = checkin_result.get(
            "message",
            "未知结果"
        )

        if not isinstance(message, str):
            message = str(message)

        # 4. 只识别明确返回的本次奖励
        g.reward_points = parse_reward(
            checkin_result
        )

        if g.reward_points is not None and g.reward_points > 0:

            rewarded_count += 1

            # 5. 签到后重新查询积分
            # 给服务器一点时间更新数据
            time.sleep(1)

            g.points_after = g.get_points()

            if g.points_before is None:
                before_text = "未知"
            else:
                before_text = str(g.points_before)

            if g.points_after is None:
                after_text = "查询失败"
            else:
                after_text = str(g.points_after)

            if g.points_before is not None and g.points_after is not None:

                actual_change = (
                    g.points_after - g.points_before
                )

                change_text = f"{actual_change:+d}"

            else:
                change_text = "未能计算"

            log(
                f"✅ 用户: {g.email} | "
                f"签到前积分: {before_text} | "
                f"本次奖励: +{g.reward_points} | "
                f"签到后积分: {after_text} | "
                f"天数: {g.left_days}"
            )

            detail = (
                f"账号：{g.email}\n"
                f"签到状态：成功，获得奖励\n"
                f"签到前积分：{before_text}\n"
                f"本次签到奖励：+{g.reward_points}\n"
                f"签到后积分：{after_text}\n"
                f"积分实际变化：{change_text}\n"
                f"会员剩余天数：{g.left_days} 天\n"
                f"签到结果：{message}\n"
                f"签到时间：{now_str()}"
            )

            results.append(detail)
            mail_results.append(detail)

        else:

            # 今日已签到、未获得奖励，或者返回结果不明确
            g.points_after = None

            log(
                f"ℹ️ 用户: {g.email} | "
                f"本次没有检测到明确的积分奖励 | "
                f"天数: {g.left_days} | "
                f"结果: {message}"
            )

            results.append(
                f"ℹ️ {g.email}\n"
                f"本次没有检测到明确的积分奖励\n"
                f"剩余天数：{g.left_days} 天\n"
                f"签到结果：{message}"
            )

        if index < len(cookies):
            time.sleep(1.5)

    # 6. PushPlus 汇总
    push_token = os.environ.get(
        "PUSHPLUS_TOKEN", ""
    ).strip()

    if push_token and results:

        push_content = "<br><br>".join(
            result.replace("\n", "<br>")
            for result in results
        )

        pushplus(
            push_token,
            f"GLaDOS 签到奖励 {rewarded_count}/{len(cookies)}",
            push_content
        )

    # 7. 只有获得明确积分奖励的账号才发 QQ 邮件
    # 每个获得奖励的账号分别发送一封邮件
    if mail_results:

        for detail in mail_results:

            first_line = detail.splitlines()[0]

            send_qq_mail(
                subject=f"GLaDOS 签到奖励通知 - {first_line}",
                content=(
                    "GLaDOS 自动签到奖励通知\n"
                    "==============================\n\n"
                    + detail
                )
            )

    else:
        log(
            "ℹ️ 本次没有账号获得明确的签到积分奖励，"
            "不发送 QQ 邮件"
        )

    log(
        f"🏁 完成：获得签到奖励 "
        f"{rewarded_count}/{len(cookies)}，"
        f"失败 {failed_count}"
    )

    # 只要至少一个账号明确获得奖励，就返回成功
    sys.exit(
        0 if rewarded_count > 0 else
        (1 if failed_count == len(cookies) else 0)
    )


if __name__ == "__main__":
    main()