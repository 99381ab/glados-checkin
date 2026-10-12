#!/usr/bin/env python3
# -*- coding: utf-8 -*-

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


def get_headers(cookie):
    user_agent = (
        os.environ.get("GLADOS_USER_AGENT", "").strip()
        or DEFAULT_USER_AGENT
    )

    return {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json;charset=UTF-8",
        "Origin": BASE_URL,
        "Referer": f"{BASE_URL}/console",
        "User-Agent": user_agent,
        "Cookie": cookie,
    }


def request(url, method, cookie, data=None):
    try:
        response = requests.request(
            method,
            url,
            headers=get_headers(cookie),
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


def find_points(data):
    """
    尝试从接口返回内容中查找积分。
    注意：不会把 days 或 leftDays 当成积分。
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
        "integral",
        "integrals",
    }

    if isinstance(data, dict):
        for key, value in data.items():
            key_name = str(key).replace("_", "").lower()

            if key_name in point_names and value is not None:
                return value

        for value in data.values():
            result = find_points(value)

            if result is not None:
                return result

    elif isinstance(data, list):
        for item in data:
            result = find_points(item)

            if result is not None:
                return result

    return None


def get_user_status(cookie):
    status_code, status = request(
        f"{BASE_URL}/api/user/status",
        "GET",
        cookie,
    )

    if status_code != 200:
        return status_code, status

    return status_code, status


def checkin(cookie, index):
    status_code, status = get_user_status(cookie)

    if status_code != 200:
        log(
            f"❌ 账号 {index} 获取状态失败: "
            f"HTTP {status_code} "
            f"{json.dumps(status, ensure_ascii=False)}"
        )

        return False, {
            "email": "?",
            "points": "接口未返回",
            "left_days": "接口未返回",
            "message": "获取状态失败",
            "result": status,
        }

    data = {}

    if isinstance(status, dict):
        data = status.get("data", {})

    if not isinstance(data, dict):
        data = {}

    email = data.get("email", "?")

    # 必须优先读取 leftDays，不能读取 days
    left_days = data.get("leftDays", "接口未返回")

    # 状态接口中尝试读取积分
    points = find_points(status)

    checkin_code, checkin_result = request(
        f"{BASE_URL}/api/user/checkin",
        "POST",
        cookie,
        {
            "token": "glados-facility.com"
        },
    )

    success = (
        checkin_code == 200
        and is_success(checkin_result)
    )

    message = (
        checkin_result.get("message", "未知")
        if isinstance(checkin_result, dict)
        else str(checkin_result)
    )

    # 如果状态接口没有积分，尝试从签到接口返回内容中读取
    if points is None:
        points = find_points(checkin_result)

    # 签到后再次获取状态，读取最新天数和积分
    latest_status_code, latest_status = get_user_status(cookie)

    if latest_status_code == 200:
        latest_data = {}

        if isinstance(latest_status, dict):
            latest_data = latest_status.get("data", {})

        if not isinstance(latest_data, dict):
            latest_data = {}

        # 仍然只读取 leftDays，不读取 days
        latest_left_days = latest_data.get("leftDays")

        if latest_left_days is not None:
            left_days = latest_left_days

        latest_points = find_points(latest_status)

        if latest_points is not None:
            points = latest_points

    if points is None:
        points = "接口未返回"

    icon = "✅" if success else "❌"

    log(
        f"{icon} 用户: {email} | "
        f"积分: {points} | "
        f"天数: {left_days} | "
        f"结果: {message}"
    )

    # 积分没有返回时，打印签到接口响应，方便确认真实字段
    if points == "接口未返回":
        log(
            "⚠️ 当前接口没有返回积分字段。"
            "签到接口返回内容如下："
        )

        log(
            json.dumps(
                checkin_result,
                ensure_ascii=False
            )
        )

    if not success:
        log(
            f"响应详情: HTTP {checkin_code}, "
            f"{json.dumps(checkin_result, ensure_ascii=False)}"
        )

    return success, {
        "email": email,
        "points": points,
        "left_days": left_days,
        "message": message,
        "result": checkin_result,
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
                f"{html.escape(str(info.get('points', '接口未返回')))}"
                f"<br>"
                f"<b>剩余天数:</b> "
                f"{html.escape(str(info.get('left_days', '接口未返回')))}"
                f"<br>"
                f"<b>结果:</b> "
                f"{html.escape(str(info.get('message', '?')))}"