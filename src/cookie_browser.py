# -*- coding: utf-8 -*-
"""
Open a real browser for fanqienovel login and save cookies to cookie.json.
Requires: pip install playwright && playwright install chromium
"""
import json
import os
import time
from typing import Callable, Optional

LOGIN_URL = 'https://fanqienovel.com/'
LOGIN_COOKIE_NAMES = ('sessionid', 'novel_web_id')


def _cookies_to_header(cookies: list) -> str:
    parts = []
    seen = set()
    for c in cookies:
        domain = c.get('domain', '')
        if 'fanqienovel' not in domain and 'fqnovel' not in domain:
            continue
        name = c.get('name')
        value = c.get('value')
        if not name or value is None or name in seen:
            continue
        seen.add(name)
        parts.append(f'{name}={value}')
    return '; '.join(parts)


def _has_login_cookies(header: str) -> bool:
    lowered = header.lower()
    return all(n in lowered for n in LOGIN_COOKIE_NAMES)


def login_and_save_cookie(
    cookie_path: str,
    profile_dir: Optional[str] = None,
    log: Callable[[str], None] = print,
    timeout_sec: int = 600,
) -> str:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise RuntimeError(
            '未安装 Playwright。请执行: pip install playwright && playwright install chromium'
        ) from e

    os.makedirs(os.path.dirname(cookie_path), exist_ok=True)
    if profile_dir:
        os.makedirs(profile_dir, exist_ok=True)

    log('正在打开浏览器，请在窗口中登录番茄小说（可随便打开一本书）…')
    log('登录成功后程序会自动保存 Cookie 并关闭浏览器。')

    with sync_playwright() as p:
        launch_kwargs = {
            'headless': False,
            'locale': 'zh-CN',
        }
        if profile_dir:
            context = p.chromium.launch_persistent_context(
                profile_dir,
                channel='msedge',
                **launch_kwargs,
            )
            page = context.pages[0] if context.pages else context.new_page()
        else:
            browser = p.chromium.launch(channel='msedge', **launch_kwargs)
            context = browser.new_context(locale='zh-CN')
            page = context.new_page()

        page.goto(LOGIN_URL, wait_until='domcontentloaded', timeout=60000)

        deadline = time.time() + timeout_sec
        cookie_header = ''
        while time.time() < deadline:
            cookies = context.cookies()
            cookie_header = _cookies_to_header(cookies)
            if _has_login_cookies(cookie_header):
                break
            if int(time.time()) % 15 == 0:
                log('等待登录…（请在浏览器中完成登录）')
            time.sleep(1)

        if not _has_login_cookies(cookie_header):
            context.close()
            raise TimeoutError(
                f'在 {timeout_sec} 秒内未检测到登录 Cookie，请重试。'
            )

        with open(cookie_path, 'w', encoding='UTF-8') as f:
            json.dump(cookie_header, f, ensure_ascii=False)

        log('Cookie 已自动保存，可以开始下载。')
        context.close()
        return cookie_header
