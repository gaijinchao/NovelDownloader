# -*- coding: utf-8 -*-
"""
导入并校验浏览器 Cookie，写入 data/cookie.json。

用法:
  cd src
  python setup_cookie.py

也可把 Cookie 粘贴到 data/cookie_raw.txt 后运行本脚本，
或: python setup_cookie.py 路径\\到\\cookie.txt
"""
import json
import os
import sys

from main import Config, NovelDownloader, normalize_cookie_value, COOKIE_SETUP_HINT

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, 'data')
COOKIE_PATH = os.path.join(DATA_DIR, 'cookie.json')
RAW_PATH = os.path.join(DATA_DIR, 'cookie_raw.txt')


def read_input_text() -> str:
    if len(sys.argv) > 1:
        path = sys.argv[1]
        with open(path, 'r', encoding='utf-8') as f:
            return f.read().strip()

    if os.path.exists(RAW_PATH):
        with open(RAW_PATH, 'r', encoding='utf-8') as f:
            text = f.read().strip()
        if text:
            print(f'已从 {RAW_PATH} 读取')
            return text

    print('请粘贴浏览器 Cookie（一整行，或 Cookie 插件导出的 JSON）。')
    print('粘贴结束后输入单独一行 END 并回车：')
    lines = []
    while True:
        line = input()
        if line.strip() == 'END':
            break
        lines.append(line)
    return '\n'.join(lines).strip()


def parse_cookie_text(text: str) -> str:
    text = text.strip()
    if not text:
        raise ValueError('Cookie 为空')
    if text.startswith('{') or text.startswith('['):
        return normalize_cookie_value(json.loads(text))
    return normalize_cookie_value(text)


def validate_cookie(cookie_str: str) -> tuple[bool, str]:
    config = Config()
    downloader = NovelDownloader(config, defer_cookie=True)
    downloader.cookie = cookie_str
    chapter_id = downloader._get_initial_chapter_id()
    if downloader._test_cookie(chapter_id, cookie_str) == 's':
        return True, '阅读页校验通过，可以下载章节。'
    return False, (
        '校验失败：阅读页无正文（常见为验证码或未登录）。\n'
        '请用浏览器打开 fanqienovel.com 登录并手动过滑动验证后重新导出 Cookie。'
    )


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    try:
        raw = read_input_text()
        cookie_str = parse_cookie_text(raw)
    except Exception as e:
        print(f'解析失败: {e}')
        print(COOKIE_SETUP_HINT)
        sys.exit(1)

    if 'novel_web_id' not in cookie_str:
        print('警告: Cookie 中未看到 novel_web_id，仍尝试校验…')

    ok, msg = validate_cookie(cookie_str)
    print(msg)
    force = '--force' in sys.argv or '-f' in sys.argv
    if not ok and not force:
        print('若仍想先保存再试 Web 下载，可加参数: python setup_cookie.py --force')
        print(COOKIE_SETUP_HINT)
        sys.exit(1)
    if not ok and force:
        print('（已强制保存，未通过阅读页校验，下载可能仍失败）')

    with open(COOKIE_PATH, 'w', encoding='UTF-8') as f:
        json.dump(cookie_str, f, ensure_ascii=False)
    print(f'已保存到: {COOKIE_PATH}')
    print('现在可运行: python -u server.py')


if __name__ == '__main__':
    main()
