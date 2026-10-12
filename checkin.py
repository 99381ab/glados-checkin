#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLaDOS automatic check-in for glados-facility.com."""

import html
import json
import os
import re
import sys
from datetime import datetime

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
                    if (
                        isinstance(item, dict)
                        and item.get("name")
                        and item.get("value") is not None
                    ):
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
    user_agent = (
        os.environ.get("GLADOS_USER_AGENT", "").strip()
        or DEFAULT_USER_AGENT
    )

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
            payload = {
                "message": response.text[:500]
            }

        return response.status_code, payload

    except requests.RequestException as exc:
        return None, {
            "error": str(exc)
        }


def is_success(result):
    if not isinstance(result, dict):
        return False

    message = str(result.get("message", "")).lower()

    return (
        result.get("code") == 0
        or any(marker in message for marker in NORMAL_MESSAGES)
    )


def find_value(data, names):
    """
    在嵌套字典中查找指定字段。
    字段名不区分大小写。
    """
    if isinstance(data, dict):
        for key, value in data.items():
            if str(key).lower() in names and value is not None:
                return value

        for value in data.values():
            result = find_value(value, names)

            if result is not None:
                return result

    elif isinstance(data, list):
        for item in data:
            result = find_value(item, names)

            if result is not None:
                return result

    return None


def get_user_info(status):
    """
    从状态接口返回内容中提取用户信息。
    兼容不同版本接口的数据结构。
    """
    if not isinstance(status, dict):
        return {}

    data = status.get("data", status)

    if isinstance(data, dict):
        return data

    return {}


def get_points(status):
    """
    兼容不同接口版本可能使用的积分字段名称。
    """
    point_names = {
        "points",
        "point",
        "totalpoints",
        "totalpoint",
        "balance",
        "credit",
        "credits",
        "score",
    }

    points = find_value(status, point_names)

    if points is None:
        return "?"

    return points


def get_left_days(status):
    """
    提取剩余天数。
    """
    day_names = {
        "leftdays",
        "left_days",
        "days",
        "remainingdays",
        "remaining_days",
    }

    left_days = find_value(status, day_names)

    if left_days is None:
        return "?"

    return left_days


def checkin(cookie, index):
    status_code, status = request(
        f"{BASE_URL}/api/user/status",
        "GET",
        cookie,
    )

    if status_code != 200:
        log(
            f"❌ 账号 {index} 获取状态失败: "
            f"HTTP {status_code} {status}"
        )

        return False, status

    user_data = get_user_info(status)

    email = find_value(
        user_data,
        {
            "email",
            "mail",
            "username",
        },
    ) or "?"

    before_points = get_points(status)
    before_left_days = get_left_days(status)

    code, result = request(
        f"{BASE_URL}/api/user/checkin",
        "POST",
        cookie,
        {
            "token": "glados-facility.com"
        },
    )

    success = code == 200 and is_success(result)

    message = (
        result.get("message", "未知")
        if isinstance(result, dict)
        else str(result)
    )

    # 签到后重新获取状态，确保显示最新积分和天数
    latest_points = before_points
    latest_left_days = before_left_days

    latest_status_code, latest_status = request(
        f"{BASE_URL}/api/user/status",
        "GET",
        cookie,
    )

    if latest_status_code == 200:
        latest_points = get_points(latest_status)
        latest_left_days = get_left_days(latest_status)

    icon = "✅" if success else "❌"

    log(
        f"{icon} 用户: {email} | "
        f"积分: {latest_points} | "
        f"天数: {latest_left_days} | "
        f"结果: {message}"
    )

    if latest_points == "?":
        log(
            "⚠️ 接口返回中没有识别到积分字段，"
            "请检查上面的接口返回数据。"
        )

        if isinstance(status, dict):
            log(
                "状态接口返回字段: "
                + json.dumps(status, ensure_ascii=False)
            )

    if not success:
        log(
            f"响应详情: HTTP {code}, "
            f"{json.dumps(result, ensure_ascii=False)}"
        )

    return success, {
        "email": email,
        "points": latest_points,
        "left_days": latest_left_days,
        "message": message,
        "result": result,
    }


def pushplus(token, title, content):
    if not token:
        return

    try:
        response = requests.post(
            "https://www.pushplus.plus/send",
            json={
                "token": token,
                "title": title,
                "content": content,
                "template": "html",
            },
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
                f"<p>"
                f"<b>账号:</b> "
                f"{html.escape(str(info.get('email', '?')))}"
                f"<br>"
                f"<b>当前积分:</b> "
                f"{html.escape(str(info.get('points', '?')))}"
                f"<br>"
                f"<b>剩余天数:</b> "
                f"{html.escape(str(info.get('left_days', '?')))}"
                f"<br>"
                f"<b>结果:</b> "
                f"{html.escape(str(info.get('message', '?')))}"
                f"</p>"
            )

    token = os.environ.get("PUSHPLUS_TOKEN", "")

    if token:
        pushplus(
            token,
            f"GLaDOS签到: 成功{success_count}/{len(cookies)}",
            "".join(reports) or "没有可推送的结果",
        )

    return 0 if success_count == len(cookies) else 1


if __name__ == "__main__":
    sys.exit(main())