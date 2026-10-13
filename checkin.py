#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
GLaDOS 自动签到脚本

功能：
1. 多账号签到，从 cookies.txt 读取 Cookie
2. 自动切换备用域名
3. 查询会员剩余天数
4. 查询签到前后积分
5. 显示本次签到奖励
6. 只有获得明确积分奖励时才发送 QQ 邮件
7. 支持 PushPlus 推送
8. 使用北京时间
9. 每个账号使用固定编号，避免邮箱被隐藏后无法区分
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


# ================= 时间与日志 =================

def now_str():
    return datetime.now(BEIJING_TZ).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def log(msg: str):
    print(f"[{now_str()}] {msg}", flush=True)


# ================= Cookie 配置 =================

def get_cookies():
    """
    从与 checkin.py 同目录的 cookies.txt 读取 Cookie。
    每行填写一个账号的完整 Cookie。
    支持空行、# 注释行和 Cookie: 前缀。
    """

    script_dir = os.path.dirname(
        os.path.abspath(__file__)
    )

    cookie_file = os.path.join(
        script_dir,
        "cookies.txt"
    )

    if not os.path.isfile(cookie_file):
        log(
            "❌ 找不到 cookies.txt，"
            "请确认它与 checkin.py 在同一目录"
        )
        return []

    try:
        with open(
            cookie_file,
            "r",
            encoding="utf-8-sig"
        ) as file:
            lines = file.readlines()

    except OSError as exc:
        log(
            f"❌ 读取 cookies.txt 失败："
            f"{type(exc).__name__}"
        )
        return []

    cookies = []

    for line in lines:
        cookie = line.strip()

        # 跳过空行和注释行
        if not cookie or cookie.startswith("#"):
            continue

        # 兼容 Cookie: xxx 的格式
        if cookie.lower().startswith("cookie:"):
            cookie = cookie[7:].strip()

        if cookie:
            cookies.append(cookie)

    if not cookies:
        log("❌ cookies.txt 中没有读取到有效 Cookie")
        return []

    log(
        f"✅ 已从 cookies.txt 读取 "
        f"{len(cookies)} 个账号"
    )

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
        """
        依次尝试备用域名。
        返回 JSON 字典，失败时返回 None。
        """

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
                headers["Referer"] = (
                    f"{domain}/console/checkin"
                )

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

                    log(
                        f"⚠️ {domain} 返回内容不是有效 JSON"
                    )

                    continue

                # 保存本次成功响应的域名
                self.domain = domain

                return result

            except requests.RequestException as exc:

                log(
                    f"⚠️ {domain} 请求失败："
                    f"{type(exc).__name__}"
                )

        return None

    # ---------- 查询账号状态 ----------

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

        # 邮箱缺失或为空时，使用 Unknown
        self.email = data.get("email") or "Unknown"

        left_days = data.get("leftDays")

        if left_days is not None:

            try:
                self.left_days = str(
                    int(float(left_days))
                )

            except (TypeError, ValueError):
                self.left_days = str(left_days)

        return True

    # ---------- 查询积分 ----------

    def get_points(self):
        """
        查询当前积分。

        支持以下两种返回结构：

        {"points": 100}

        {"data": {"points": 100}}

        如果接口结构不同，则返回 None。
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

    # ---------- 执行签到 ----------

    def checkin(self):

        return self._request(
            "POST",
            "/api/user/checkin",
            {"token": "glados.cloud"}
        )


# ================= 解析签到奖励 =================

def parse_reward(result):
    """
    从签到接口的 message 中提取积分奖励。

    例如：
    Checkin! Got 5 Points -> 5

    如果没有明确的积分奖励信息，返回 None。
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


def is_already_checked_in(message):
    """
    判断接口是否明确表示今天已经签到。
    """

    if not isinstance(message, str):
        return False

    text = message.lower()

    return (
        "return tomorrow" in text
        or "already checked" in text
        or "already checkin" in text
    )


# ================= PushPlus 推送 =================

def pushplus(token, title, content):

    if not token:
        return False

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

            return True

        log(
            f"⚠️ PushPlus 返回 HTTP "
            f"{response.status_code}"
        )

        return False

    except requests.RequestException as exc:

        log(
            f"❌ PushPlus 发送失败："
            f"{type(exc).__name__}"
        )

        return False


# ================= QQ 邮件 =================

def send_qq_mail(subject, content):

    smtp_user = os.environ.get(
        "QQ_SMTP_USER",
        ""
    ).strip()

    smtp_auth_code = os.environ.get(
        "QQ_SMTP_AUTH_CODE",
        ""
    ).strip()

    mail_to = os.environ.get(
        "QQ_MAIL_TO",
        ""
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

        # 固定账号编号，不依赖邮箱是否正常显示
        account_label = f"账号 {index}"

        # 1. 查询账号状态
        if not g.get_status():

            log(
                f"❌ {account_label} 状态查询失败，"
                f"Cookie 可能失效"
            )

            results.append(
                f"❌ {account_label}：状态查询失败"
            )

            failed_count += 1

            if index < len(cookies):
                time.sleep(1.5)

            continue

        # 统一账号显示格式
        account_display = (
            f"{account_label}（{g.email}）"
        )

        # 2. 查询签到前积分
        g.points_before = g.get_points()

        if g.points_before is None:

            log(
                f"⚠️ {account_display} "
                f"签到前积分查询失败"
            )

        # 3. 执行签到
        checkin_result = g.checkin()

        if not isinstance(checkin_result, dict):

            log(
                f"❌ {account_display} "
                f"签到请求失败"
            )

            # 请求失败后仍尝试查询当前积分
            g.points_after = g.get_points()

            points_text = (
                str(g.points_after)
                if g.points_after is not None
                else "查询失败"
            )

            results.append(
                f"❌ {account_display}\n"
                f"签到请求失败\n"
                f"当前积分：{points_text}\n"
                f"剩余天数：{g.left_days} 天"
            )

            failed_count += 1

            if index < len(cookies):
                time.sleep(1.5)

            continue

        # 4. 读取签到结果
        message = checkin_result.get(
            "message",
            "未知结果"
        )

        if not isinstance(message, str):
            message = str(message)

        # 5. 解析本次奖励
        g.reward_points = parse_reward(
            checkin_result
        )

        # 给服务器一点时间更新积分
        time.sleep(1)

        # 6. 无论本次是否获得奖励，都重新查询积分
        g.points_after = g.get_points()

        # 7. 整理积分数据
        if g.points_before is None:
            before_text = "查询失败"
        else:
            before_text = str(g.points_before)

        if g.points_after is None:
            after_text = "查询失败"
        else:
            after_text = str(g.points_after)

        if (
            g.points_before is not None
            and g.points_after is not None
        ):

            actual_change = (
                g.points_after - g.points_before
            )

            change_text = f"{actual_change:+d}"

        else:
            change_text = "无法计算"

        # 8. 判断是否获得明确的签到奖励
        if (
            g.reward_points is not None
            and g.reward_points > 0
        ):

            rewarded_count += 1

            status_text = "签到成功，获得积分"

            reward_text = f"+{g.reward_points}"

            log(
                f"✅ 用户: {account_display} | "
                f"签到前积分: {before_text} | "
                f"本次奖励: {reward_text} | "
                f"签到后积分: {after_text} | "
                f"实际变化: {change_text} | "
                f"天数: {g.left_days} | "
                f"结果: {message}"
            )

        elif is_already_checked_in(message):

            status_text = "今日已签到"

            reward_text = "0"

            log(
                f"ℹ️ 用户: {account_display} | "
                f"签到状态: 今日已签到 | "
                f"当前积分: {after_text} | "
                f"本次奖励: 0 | "
                f"积分变化: {change_text} | "
                f"天数: {g.left_days} | "
                f"结果: {message}"
            )

        else:

            status_text = "未确认获得奖励"

            reward_text = "未知"

            log(
                f"⚠️ 用户: {account_display} | "
                f"签到状态: {status_text} | "
                f"当前积分: {after_text} | "
                f"本次奖励: 未知 | "
                f"积分变化: {change_text} | "
                f"天数: {g.left_days} | "
                f"结果: {message}"
            )

        # 9. 生成账号详情
        detail = (
            f"账号：{account_display}\n"
            f"签到状态：{status_text}\n"
            f"签到前积分：{before_text}\n"
            f"本次获得积分：{reward_text}\n"
            f"签到后积分：{after_text}\n"
            f"积分实际变化：{change_text}\n"
            f"会员剩余天数：{g.left_days} 天\n"
            f"签到结果：{message}\n"
            f"签到时间：{now_str()}"
        )

        results.append(detail)

        # 10. 只有本次明确获得正数奖励，才加入邮件队列
        if (
            g.reward_points is not None
            and g.reward_points > 0
        ):

            mail_results.append(detail)

        # 限速
        if index < len(cookies):
            time.sleep(1.5)

    # ================= PushPlus 汇总 =================

    push_token = os.environ.get(
        "PUSHPLUS_TOKEN",
        ""
    ).strip()

    if push_token and results:

        push_content = "<br><br>".join(
            item.replace("\n", "<br>")
            for item in results
        )

        pushplus(
            push_token,
            f"GLaDOS 签到奖励 {rewarded_count}/{len(cookies)}",
            push_content
        )

    # ================= QQ 邮件通知 =================

    if mail_results:

        mail_content = (
            "GLaDOS 自动签到奖励通知\n"
            "================================\n\n"
            + "\n\n--------------------------------\n\n".join(
                mail_results
            )
        )

        send_qq_mail(
            subject=(
                f"GLaDOS 签到奖励通知 "
                f"{rewarded_count}/{len(cookies)}"
            ),
            content=mail_content
        )

    else:

        log(
            "ℹ️ 本次没有账号获得明确的签到积分奖励，"
            "不发送 QQ 邮件"
        )

    # ================= 运行结果 =================

    log(
        f"🏁 完成 | "
        f"账号总数: {len(cookies)} | "
        f"获得奖励: {rewarded_count} | "
        f"失败: {failed_count}"
    )

    # 如果全部账号状态查询或签到请求都失败，返回失败
    if failed_count == len(cookies):
        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
