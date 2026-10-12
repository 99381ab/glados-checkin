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

SUCCESS_MESSAGES = (
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

    raw = re.sub(
        r"^cookie\s*:\s*",
        "",
        raw,
        flags=re.IGNORECASE,
    )

    if "koa:sess=" in raw or "gld:sess=" in raw:
        return raw

    if raw.startswith("{") or raw.startswith("["):
        try:
            parsed = json.loads(raw)

            if isinstance(parsed, list):
                pairs = []

                for item in parsed:
                    if not isinstance(item, dict):
                        continue

                    name = item.get("name")
                    value = item.get("value")

                    if name and value is not None:
                        pairs.append(f"{name}={value}")

                if pairs:
                    return "; ".join(pairs)

            if isinstance(parsed, dict):
                token = parsed.get("token")

                if token:
                    return f"koa:sess={token}"

        except Exception:
            return None

    return raw


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
        cookie = extract_cookie(item)

        if cookie:
            cookies.append(cookie)

    return cookies


def get_headers(cookie):
    user_agent = os.environ.get(
        "GLADOS_USER_AGENT",
        "",
    ).strip()

    if not user_agent:
        user_agent = DEFAULT_USER_AGENT

    return {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json;charset=UTF-8",
        "Origin": BASE_URL,
        "Referer": f"{BASE_URL}/console",
        "User-Agent": user_agent,
        "Cookie": cookie,