# -*- coding: utf-8 -*-
import requests as req
try:
    from curl_cffi import requests as curl_req
    HAS_CURL_CFFI = True
except ImportError:
    curl_req = None
    HAS_CURL_CFFI = False
from lxml import etree
import json
import time
import random
import os
import threading
from typing import Callable, Optional, Dict, List
from dataclasses import dataclass


COOKIE_SETUP_HINT = (
    "下载章节需要浏览器导出的完整 Cookie。请阅读 src/COOKIE_SETUP.md ，"
    "或在 src 目录运行: python setup_cookie.py"
)
COOKIE_AUTO_MAX_ATTEMPTS = 500


class CookieInitError(Exception):
    """Raised when cookie cannot be initialized for chapter downloads."""


def normalize_cookie_value(raw) -> str:
    """Normalize cookie from str, dict, or list (browser export formats)."""
    if raw is None:
        return ''
    if isinstance(raw, str):
        return raw.strip().strip('"').strip("'")
    if isinstance(raw, list):
        return '; '.join(
            f"{c['name']}={c['value']}"
            for c in raw
            if isinstance(c, dict) and c.get('name') and c.get('value') is not None
        )
    if isinstance(raw, dict):
        if 'cookie' in raw and isinstance(raw['cookie'], str):
            return raw['cookie'].strip()
        if 'cookies' in raw:
            cookies = raw['cookies']
            if isinstance(cookies, str):
                return cookies.strip()
            if isinstance(cookies, list):
                return '; '.join(
                    f"{c['name']}={c['value']}"
                    for c in cookies
                    if isinstance(c, dict) and c.get('name') and c.get('value') is not None
                )
        parts = [f"{k}={v}" for k, v in raw.items() if v is not None and k != 'cookie']
        if parts:
            return '; '.join(parts)
    return str(raw).strip()


def load_cookie_from_env() -> Optional[str]:
    value = os.environ.get('FANQIE_COOKIE', '').strip()
    return normalize_cookie_value(value) if value else None


def load_cookie_from_file(path: str) -> Optional[str]:
    if not os.path.exists(path):
        return None
    with open(path, 'r', encoding='UTF-8') as f:
        data = json.load(f)
    value = normalize_cookie_value(data)
    return value if value else None


@dataclass
class Config:
    delay: List[int] = None
    save_path: str = ''
    xc: int = 16
    chapter_start: int = 1   # 1-based 起始章
    chapter_end: int = 10    # 1-based 结束章，0 = 至全书末尾

    def __post_init__(self):
        if self.delay is None:
            self.delay = [50, 150]


class NovelDownloader:
    def __init__(self,
                 config: Config,
                 progress_callback: Optional[Callable] = None,
                 log_callback: Optional[Callable] = None,
                 defer_cookie: bool = False):
        self.config = config
        self.progress_callback = progress_callback or self._default_progress
        self.log_callback = log_callback or print
        self.defer_cookie = defer_cookie
        self.cookie_ready = False

        # Initialize headers first
        self.headers_lib = [
            {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/93.0.4577.63 Safari/537.36'},
            {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:91.0) Gecko/20100101 Firefox/91.0'},
            {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/93.0.4577.63 Safari/537.36 Edg/93.0.961.47'}
        ]
        self.headers = random.choice(self.headers_lib)

        # Use absolute paths based on script location
        self.script_dir = os.path.dirname(os.path.abspath(__file__))
        self.data_dir = os.path.join(self.script_dir, 'data')
        self.bookstore_dir = os.path.join(self.data_dir, 'bookstore')
        self.cookie_path = os.path.join(self.data_dir, 'cookie.json')

        self.CODE = [[58344, 58715], [58345, 58716]]

        # Load charset for text decoding
        charset_path = os.path.join(self.script_dir, 'charset.json')
        with open(charset_path, 'r', encoding='UTF-8') as f:
            self.charset = json.load(f)

        self._setup_directories()
        if not HAS_CURL_CFFI and not defer_cookie:
            self.log_callback(
                '提示: 未安装 curl_cffi，易触发验证码。建议: pip install curl_cffi'
            )
        self.cookie = ''
        if defer_cookie:
            preset = load_cookie_from_env() or load_cookie_from_file(self.cookie_path)
            if preset:
                self.cookie = preset
        else:
            self._init_cookie()
            self.cookie_ready = True

        # Add these variables
        self.zj = {}  # For storing chapter data
        self.book_json_path = None  # Current book's JSON path
        # 限制同时请求阅读页的数量，降低触发验证码概率
        self._chapter_request_semaphore = threading.Semaphore(
            max(1, min(getattr(self.config, 'xc', 2), 3))
        )

    def _setup_directories(self):
        """Create necessary directories if they don't exist"""
        os.makedirs(self.data_dir, exist_ok=True)
        os.makedirs(self.bookstore_dir, exist_ok=True)

    def ensure_cookie_ready(self, browser_profile_dir: Optional[str] = None):
        """Initialize cookie on first use (for deferred web startup)."""
        if self.cookie_ready:
            return
        try:
            self._init_cookie()
        except CookieInitError:
            if os.environ.get('FANQIE_SKIP_BROWSER_LOGIN', '').strip() in ('1', 'true', 'yes'):
                raise
            from cookie_browser import login_and_save_cookie
            profile = browser_profile_dir or os.path.join(self.data_dir, 'browser_profile')
            self.log_callback('Cookie 无效，正在打开浏览器请登录番茄小说…')
            login_and_save_cookie(
                self.cookie_path,
                profile_dir=profile,
                log=self.log_callback,
            )
            self._init_cookie()
        self.cookie_ready = True

    def _persist_cookie(self):
        os.makedirs(self.data_dir, exist_ok=True)
        with open(self.cookie_path, 'w', encoding='UTF-8') as f:
            json.dump(self.cookie, f, ensure_ascii=False)

    def _load_stored_cookie(self) -> Optional[str]:
        env_cookie = load_cookie_from_env()
        if env_cookie:
            return env_cookie
        return load_cookie_from_file(self.cookie_path)

    def _is_full_browser_cookie(self, cookie: str) -> bool:
        lowered = cookie.lower()
        return 'novel_web_id=' in lowered and 'sessionid=' in lowered

    def _request_headers(self, cookie: Optional[str] = None) -> dict:
        headers = self.headers.copy()
        headers.update({
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Referer': 'https://fanqienovel.com/',
        })
        value = cookie if cookie is not None else self.cookie
        if value:
            headers['cookie'] = value
        return headers

    def _session_get(self, url: str, headers: dict, params: Optional[dict] = None,
                     timeout: int = 15):
        """HTTP GET with Chrome TLS fingerprint when curl_cffi is available."""
        if HAS_CURL_CFFI:
            return curl_req.get(
                url,
                headers=headers,
                params=params,
                timeout=timeout,
                impersonate='chrome120',
            )
        return req.get(url, headers=headers, params=params, timeout=timeout)

    def _probe_homepage_cookie(self, cookie: str) -> bool:
        headers = self._request_headers(cookie)
        try:
            response = self._session_get('https://fanqienovel.com', headers=headers, timeout=10)
            return response.status_code == 200 and len(response.text) > 200
        except Exception:
            return False

    def _init_cookie(self):
        """Initialize cookie for downloads"""
        self.log_callback('正在初始化 Cookie…')
        tzj = self._get_initial_chapter_id()
        self.tzj = tzj

        stored = self._load_stored_cookie()
        if stored:
            self.cookie = stored
            if self._test_cookie(tzj, self.cookie) == 's':
                self.log_callback('Cookie 有效（阅读页校验通过）')
                return
            if self._is_full_browser_cookie(stored):
                self.log_callback(
                    '已加载浏览器完整 Cookie（程序侧阅读页校验未通过，常见于验证码）。'
                    '将直接使用该 Cookie 尝试下载；若失败请在浏览器重新过验证后导出。'
                )
                return
            self.log_callback(
                '已保存的 Cookie 无法读取章节（可能过期或不全）。'
                '将尝试自动生成；若仍失败请用浏览器 Cookie。'
            )

        if os.environ.get('FANQIE_SKIP_AUTO_COOKIE', '').strip() in ('1', 'true', 'yes'):
            raise CookieInitError(
                '已禁用自动获取 Cookie，且当前 Cookie 无效。' + COOKIE_SETUP_HINT
            )

        self._get_new_cookie(tzj)
        self.log_callback('Cookie 获取成功')

    def _default_progress(self, current: int, total: int, desc: str = '',
                          chapter_title: str = None) -> None:
        """Fallback progress hook when no web callback is provided."""
        pass

    def _download_chapter(self, title: str, chapter_id: str, existing_content: Dict) -> Optional[str]:
        """Download a single chapter with retries"""
        if title in existing_content:
            self.zj[title] = existing_content[title]  # Add this
            return existing_content[title]

        self.log_callback(f'下载章节: {title}')
        retries = 5
        captcha_waits = 0
        last_error = None

        while retries > 0:
            try:
                time.sleep(random.randint(
                    self.config.delay[0],
                    self.config.delay[1]
                ) / 1000)

                content = self._download_chapter_content(chapter_id)
                if content == 'captcha':
                    captcha_waits += 1
                    if captcha_waits > 6:
                        raise Exception(
                            '多次触发验证码：请先停止下载，在浏览器打开 fanqienovel.com '
                            '手动完成验证并登录，然后安装 curl_cffi 后重试'
                        )
                    wait_sec = min(90, random.randint(15, 30) + captcha_waits * 10)
                    self.log_callback(
                        f'章节 {title} 触发验证码，等待 {wait_sec} 秒后重试…'
                    )
                    time.sleep(wait_sec)
                    continue

                if content == 'err' or not content:
                    raise Exception('Download failed')

                self.zj[title] = content
                return content

            except Exception as e:
                last_error = e
                retries -= 1
                if retries == 0:
                    self.log_callback(f'下载失败 {title}: {str(e)}')
                    break
                time.sleep(random.randint(2, 5))

        if last_error:
            raise last_error
        return None

    def _test_cookie(self, chapter_id: int, cookie: str) -> str:
        """Test if cookie is valid"""
        self.cookie = cookie
        if len(self._download_chapter_content(chapter_id, test_mode=True)) > 200:
            return 's'
        return 'err'

    def _get_chapter_list(self, novel_id: int) -> tuple:
        """Get novel info and chapter list"""
        url = f'https://fanqienovel.com/page/{novel_id}'
        response = req.get(url, headers=self.headers)
        ele = etree.HTML(response.text)

        chapters = {}
        a_elements = ele.xpath('//div[@class="chapter"]/div/a')
        if not a_elements:  # Add this check
            return 'err', {}, []

        for a in a_elements:
            href = a.xpath('@href')
            if not href:  # Add this check
                continue
            chapters[a.text] = href[0].split('/')[-1]

        title = ele.xpath('//h1/text()')
        status = ele.xpath('//span[@class="info-label-yellow"]/text()')

        if not title or not status:  # Check both title and status
            return 'err', {}, []

        return title[0], chapters, status

    def _fetch_chapter_via_api(self, chapter_id, headers: dict, test_mode: bool = False):
        """Fetch chapter JSON API (often less captcha than reader HTML)."""
        try:
            response = self._session_get(
                'https://fanqienovel.com/api/reader/full',
                headers={**headers, 'Accept': 'application/json, text/plain, */*'},
                params={'itemId': str(chapter_id)},
                timeout=15,
            )
            if not response.text.strip():
                return None
            if 'TTGCaptcha' in response.text:
                return 'captcha'
            payload = json.loads(response.text)
            raw = payload['data']['chapterData']['content']
            if test_mode:
                return raw if isinstance(raw, str) else str(raw)
            return self._decode_content(raw)
        except Exception:
            return None

    def _download_chapter_content(self, chapter_id: int, test_mode: bool = False) -> str:
        """Download content with fallback and better error handling"""
        headers = self._request_headers()
        reader_url = (
            f'https://fanqienovel.com/reader/{chapter_id}'
            '?enter_from=page'
        )

        for attempt in range(3):
            try:
                with self._chapter_request_semaphore:
                    response = self._session_get(
                        reader_url,
                        headers=headers,
                        timeout=15,
                    )
                response.raise_for_status()

                content = '\n'.join(
                    etree.HTML(response.text).xpath(
                        '//div[@class="muye-reader-content noselect"]//p/text()'
                    )
                )

                if not content.strip():
                    if 'TTGCaptcha' in response.text:
                        api_content = self._fetch_chapter_via_api(
                            chapter_id, headers, test_mode,
                        )
                        if api_content and api_content not in ('captcha', 'err'):
                            return api_content
                        if test_mode:
                            return 'err'
                        return 'captcha'
                    if test_mode:
                        return 'err'
                    return 'captcha'

                if test_mode:
                    return content

                try:
                    return self._decode_content(content)
                except:
                    # Try alternative decoding mode
                    try:
                        return self._decode_content(content, mode=1)
                    except:
                        # Fallback HTML processing
                        content = content[6:]
                        tmp = 1
                        result = ''
                        for i in content:
                            if i == '<':
                                tmp += 1
                            elif i == '>':
                                tmp -= 1
                            elif tmp == 0:
                                result += i
                            elif tmp == 1 and i == 'p':
                                result = (result + '\n').replace('\n\n', '\n')
                        return result

            except Exception as e:
                api_content = self._fetch_chapter_via_api(
                    chapter_id, headers, test_mode,
                )
                if api_content and api_content not in ('captcha', 'err'):
                    return api_content
                if attempt == 2:
                    if test_mode:
                        return 'err'
                    hint = ''
                    if not HAS_CURL_CFFI:
                        hint = '（必须安装 curl_cffi: pip install curl_cffi，并重启 server.py）'
                    raise Exception(
                        f"Download failed after 3 attempts: {str(e)}{hint}"
                    )
                time.sleep(2 + attempt)

    def _decode_content(self, content: str, mode: int = 0) -> str:
        """Decode novel content using both charset modes"""
        result = ''
        for char in content:
            uni = ord(char)
            if self.CODE[mode][0] <= uni <= self.CODE[mode][1]:
                bias = uni - self.CODE[mode][0]
                if 0 <= bias < len(self.charset[mode]) and self.charset[mode][bias] != '?':
                    result += self.charset[mode][bias]
                else:
                    result += char
            else:
                result += char
        return result

