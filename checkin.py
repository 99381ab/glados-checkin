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
    """输出带时间的日志。"""
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}")


def extract_cookie(raw):
    """清理并提取 Cookie。"""
    if not raw:
        return None

    raw = raw.strip()

    raw = re.sub(
        r"^cookie\s*:\s*",
        "",
        raw,
        flags=re.IGNORECASE,
    )

    if "gld:sess=" in raw or "koa:sess=" in raw:
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
                        pairs.append(
                            f"{item['name']}={item['value']}"
                        )

                return "; ".join(pairs) or None

            if isinstance(parsed, dict):
                token = parsed.get("token")

                if token:
                    return f"koa:sess={token}"

        except (
            json.JSONDecodeError,
            TypeError,
            AttributeError,
        ):
            return None

    return raw


def get_cookies():
    """从环境变量中读取一个或多个 Cookie。"""
    raw = os.environ.get("GLADOS_COOKIE", "").strip()

    if not raw:
        log("❌ 未配置 GLADOS_COOKIE")
        return []

    if "\n" in raw:
        separator = "\n"
    else:
        separator = "&"

    cookies = []

    for item in raw.split(separator):
        cookie = extract_cookie(item)

        if cookie:
            cookies.append(cookie)

    return cookies


def get_headers(cookie):
    """生成请求头。"""
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
    """发送 HTTP 请求并解析 JSON。"""
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
                "message": response.text[:1000]
            }

        return response.status_code, payload

    except requests.RequestException as exc:
        return None, {
            "error": str(exc)
        }


def is_success(result):
    """判断签到是否成功。"""
    if not isinstance(result, dict):
        return False

    message = str(
        result.get("message", "")
    ).lower()

    return (
        result.get("code") == 0
        or any(
            marker in message
            for marker in NORMAL_MESSAGES
        )
    )


def find_value(data, target_names):
    """
    在嵌套字典或列表中查找字段。
    字段名会统一转成小写并去掉下划线。
    """
    if isinstance(data, dict):
        for key, value in data.items():
            normalized_key = (
                str(key)
                .replace("_", "")
                .lower()
            )

            if normalized_key in target_names:
                return value

        for value in data.values():
            result = find_value(
                value,
                target_names,
            )

            if result is not None:
                return result

    elif isinstance(data, list):
        for item in data:
            result = find_value(
                item,
                target_names,
            )

            if result is not None:
                return result

    return None


def find_points(data):
    """
    查找积分字段。

    注意：
    当前接口返回的数据里可能没有积分字段，
    所以找不到时返回 None。
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

    return find_value(
        data,
        point_names,
    )


def get_status(cookie):
    """获取用户状态。"""
    return request(
        f"{BASE_URL}/api/user/status",
        "GET",
        cookie,
    )


def get_status_data(status):
    """提取状态接口中的 data。"""
    if not isinstance(status, dict):
        return {}

    data = status.get("data", {})

    if isinstance(data, dict):