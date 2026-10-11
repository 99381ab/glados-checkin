#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLaDOS automatic check-in for glados-facility.com."""

import html
import json
import os
import re
import smtplib
import sys
from datetime import datetime
from email.mime.text import MIMEText

import requests

if sys.platform.startswith("win"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE_URL = "https://glados-facility.com"
DOMAINS = [BASE_URL]

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/143.0.0.0 Safari/537.36 Edg/143.0.0.0"
)

NORMAL_MESSAGES = (
    "checkin",
    "repeats",
    "observation logged",
)


def log(message):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}")


def extract_cookie(raw):
    if not raw:
        return None
    raw = raw.strip()
    raw = re.sub(r"^cookie\s*:\s*", "", raw, flags=re.IGNORECASE)

    if any(name in raw for name in ("gld:sess=", "koa:sess=")):
        return raw

    if raw.startswith(("{", "[")):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                pairs = []
                for item in parsed:
                    if isinstance(item, dict) and item.get("name") and item.get("value") is not None:
                        pairs.append(f"{item['name']}={item['value']}")
                return "; ".join(pairs) or None
            if isinstance(parsed, dict) and parsed.get("token"):
                return f"koa:sess={parsed['token']}"
        except (json.JSONDecodeError, TypeError, AttributeError):
            return None

    return raw


def get_cookies():
    raw = os.environ.get("GLADOS_COOKIE", "").strip()
    if not raw:
        log("❌ 未配置 GLADOS_COOKIE")
        return []

    separator = "\n" if "\n" in raw else "&"
    cookies = []
    for item in raw.split(separator):
        cookie = extract_cookie(item)
        if cookie:
            cookies.append(cookie)
    return cookies


def get_headers(cookie, domain):
    user_agent = os.environ.get("GLADOS_USER_AGENT", "").strip() or DEFAULT_USER_AGENT
    return {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json;charset=UTF-8",
        "Origin": domain,
        "Referer": f"{domain}/console",
        "User-Agent": user_agent,
        "Cookie": cookie,
    }


def request(url, method, cookie, data=None):
    domain = BASE_URL
    try:
        response = requests.request(
            method,
            url,
            headers=get_headers(cookie, domain),
            json=data,
            timeout=20,
        )
        try:
            payload = response.json()
        except ValueError:
            payload = {"message": response.text[:500]}
        return response.status_code, payload
    except requests.RequestException as exc:
        return None, {"error": str(exc)}


def is_success(result):
    if not isinstance(result, dict):
        return False
    message = str(result.get("message", "")).lower()
    return result.get("code") == 0 or any(marker in message for marker in NORMAL_MESSAGES)


def checkin(cookie, index):
    email = "?"
    points = "?"
    left_days = "?"
    status_code, status = request(f"{BASE_URL}/api/user/status", "GET", cookie)
    if status_code != 200:
        message = f"获取状态失败: HTTP {status_code}"
        log(f"❌ 账号 {index} {message} {status}")
        return False, {
            "index": index,
            "email": email,
            "points": points,
            "left_days": left_days,
            "message": message,
            "result": status,
        }

    user_data = status.get("data", {}) if isinstance(status, dict) else {}
    if isinstance(user_data, dict):
        email = user_data.get("email", "?")
        points = user_data.get("points", "?")
        left_days = user_data.get("leftDays", "?")

    code, result = request(
        f"{BASE_URL}/api/user/checkin",
        "POST",
        cookie,
        {"token": "glados-facility.com"},
    )

    success = code == 200 and is_success(result)
    message = result.get("message", "未知") if isinstance(result, dict) else str(result)
    icon = "✅" if success else "❌"
    log(f"{icon} 用户: {email} | 积分: {points} | 天数: {left_days} | 结果: {message}")
    if not success:
        log(f"响应详情: HTTP {code}, {result}")
    return success, {
        "index": index,
        "email": email,
        "points": points,
        "left_days": left_days,
        "message": message,
        "result": result,
    }


def send_email_notification(success_count, total_count, reports_html):
    smtp_user = os.environ.get("SMTP_USER", "").strip()
    smtp_password = os.environ.get("SMTP_PASSWORD", "").strip()
    email_to = os.environ.get("EMAIL_TO", "").strip()
    if not (smtp_user and smtp_password and email_to):
        log("ℹ️ 未完整配置 SMTP_USER/SMTP_PASSWORD/EMAIL_TO，跳过邮件通知")
        return

    smtp_host = os.environ.get("SMTP_HOST", "").strip() or "smtp.qq.com"
    smtp_port_text = os.environ.get("SMTP_PORT", "").strip() or "465"
    try:
        smtp_port = int(smtp_port_text)
    except ValueError:
        smtp_port = 465

    recipients = [item.strip() for item in re.split(r"[,\n;]+", email_to) if item.strip()]
    if not recipients:
        log("ℹ️ EMAIL_TO 无有效收件人，跳过邮件通知")
        return

    subject_prefix = os.environ.get("EMAIL_SUBJECT_PREFIX", "").strip() or "GLaDOS签到"
    subject = f"{subject_prefix} 成功{success_count}/{total_count}"
    content = (
        f"<p><b>签到汇总:</b> 成功 {success_count}/{total_count}</p>"
        f"{reports_html or '<p>没有可推送的结果</p>'}"
    )

    msg = MIMEText(content, "html", "utf-8")
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = ", ".join(recipients)

    try:
        with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=20) as server:
            server.login(smtp_user, smtp_password)
            server.sendmail(smtp_user, recipients, msg.as_string())
        log("✅ 邮件通知发送成功")
    except Exception as exc:
        log(f"❌ 邮件通知发送失败: {exc}")


def pushplus(token, title, content):
    if not token:
        return
    try:
        response = requests.post(
            "https://www.pushplus.plus/send",
            json={"token": token, "title": title, "content": content, "template": "html"},
            timeout=10,
        )
        response.raise_for_status()
        log("✅ PushPlus 推送成功")
    except requests.RequestException as exc:
        log(f"❌ PushPlus 推送失败: {exc}")


def main():
    log("🚀 GLaDOS glados-facility.com 签到开始")
    cookies = get_cookies()
    if not cookies:
        return 1

    success_count = 0
    reports = []
    for index, cookie in enumerate(cookies, 1):
        success, info = checkin(cookie, index)
        if success:
            success_count += 1
        if isinstance(info, dict):
            reports.append(
                f"<p><b>账号{html.escape(str(info.get('index', index)))}:</b> "
                f"{html.escape(str(info.get('email', '?')))}<br>"
                f"<b>积分:</b> {html.escape(str(info.get('points', '?')))}<br>"
                f"<b>剩余天数:</b> {html.escape(str(info.get('left_days', '?')))}<br>"
                f"<b>结果:</b> {html.escape(str(info.get('message', '?')))}</p>"
            )

    report_content = (
        f"<p><b>签到汇总:</b> 成功 {success_count}/{len(cookies)}</p>"
        f"{''.join(reports) or '<p>没有可推送的结果</p>'}"
    )

    token = os.environ.get("PUSHPLUS_TOKEN", "")
    if token:
        pushplus(
            token,
            f"GLaDOS签到: 成功{success_count}/{len(cookies)}",
            report_content,
        )
    send_email_notification(success_count, len(cookies), "".join(reports))

    return 0 if success_count == len(cookies) else 1


if __name__ == "__main__":
    sys.exit(main())
