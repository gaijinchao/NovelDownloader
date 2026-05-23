from gevent import monkey
monkey.patch_all()

from flask import Flask, render_template, jsonify, send_file, request
from flask_socketio import SocketIO
from main import NovelDownloader, Config, CookieInitError, COOKIE_SETUP_HINT
import os
import threading
import logging
from collections import deque
import time
import json
import re
from functools import wraps
import random
import traceback
import webbrowser
import socket
from port_utils import ensure_port_free

# Web 版禁用暴力猜 Cookie，首次下载时通过浏览器登录
os.environ.setdefault('FANQIE_SKIP_AUTO_COOKIE', '1')
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 首先创建配置实例
config = Config()

# 路径（源码运行）
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_ROOT = BASE_DIR
config.save_path = os.path.join(DATA_ROOT, 'novel_downloads')
config.bookstore_dir = os.path.join(DATA_ROOT, 'data', 'bookstore')

# 创建必要的目录
DATA_DIR = os.path.join(DATA_ROOT, 'data')
BOOKSTORE_DIR = os.path.join(DATA_DIR, 'bookstore')
DOWNLOADS_DIR = os.path.join(DATA_ROOT, 'novel_downloads')
CONFIG_FILE = os.path.join(DATA_DIR, 'web_config.json')

# 确保所有必要的目录存在
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(BOOKSTORE_DIR, exist_ok=True)
os.makedirs(DOWNLOADS_DIR, exist_ok=True)

app = Flask(__name__,
            static_folder=os.path.join(BASE_DIR, 'static'),
            template_folder=os.path.join(BASE_DIR, 'templates'))
app.config['SECRET_KEY'] = 'fanqie_novel_downloader'  # Add secret key
socketio = SocketIO(app, async_mode='gevent', cors_allowed_origins='*')

# Web 版固定：单 TXT、顺序下载
config.save_path = DOWNLOADS_DIR
config.bookstore_dir = BOOKSTORE_DIR
config.xc = 1
config.delay = [int(os.environ.get('FANQIE_DELAY_MIN', '1500')),
                int(os.environ.get('FANQIE_DELAY_MAX', '3500'))]
config.chapter_start = 1
config.chapter_end = int(os.environ.get('FANQIE_MAX_CHAPTERS', '10'))

class NovelDownloaderWrapper(NovelDownloader):
    def ensure_cookie_ready(self, browser_profile_dir=None):
        super().ensure_cookie_ready(browser_profile_dir=browser_profile_dir)
        _refresh_cookie_state(emit_update=True)

    def _load_progress(self, json_path: str) -> dict:
        if not os.path.exists(json_path):
            return {}
        try:
            with open(json_path, 'r', encoding='UTF-8') as f:
                data = json.load(f)
            chapters = data.get('chapters', {})
            return {
                k: v for k, v in chapters.items()
                if k != '_failed_chapters' and v
            }
        except Exception as e:
            logger.warning('无法读取断点文件 %s: %s', json_path, e)
            return {}

    def _save_progress_files(
        self,
        novel_id,
        name: str,
        all_chapters: dict,
        chapters_scope: dict,
        novel_content: dict,
        json_path: str,
        txt_path: str,
        *,
        status: str = 'downloading',
    ):
        os.makedirs(os.path.dirname(json_path), exist_ok=True)
        os.makedirs(os.path.dirname(txt_path), exist_ok=True)
        chapters_only = {
            k: v for k, v in novel_content.items() if k != '_failed_chapters'
        }
        novel_data = {
            '_meta': {
                'novel_id': str(novel_id),
                'name': name,
                'download_time': time.strftime('%Y-%m-%d %H:%M:%S'),
                'total_chapters': len(all_chapters),
                'completed_chapters': len(chapters_only),
                'status': status,
            },
            'chapters': chapters_only,
        }
        with open(json_path, 'w', encoding='UTF-8') as f:
            json.dump(novel_data, f, ensure_ascii=False, indent=2)
        with open(txt_path, 'w', encoding='UTF-8') as f:
            f.write(f"《{name}》\n\n")
            for title in all_chapters.keys():
                content = chapters_only.get(title) or chapters_only.get(title.strip())
                if content:
                    f.write(f"\n{title}\n\n{content}\n")

    def download_novel(self, novel_id: int) -> str:
        """Download novel as single TXT with pause/cancel/resume support."""
        try:
            self.ensure_cookie_ready(
                browser_profile_dir=os.path.join(DATA_DIR, 'browser_profile')
            )
            name, chapters, status = self._get_chapter_list(novel_id)
            if name == 'err':
                return 'err'

            start, end = _resolve_download_range(novel_id)
            chapters_scope = _chapter_range(chapters, start, end)
            if not chapters_scope:
                self.log_callback(f'章节范围无效（第 {start}–{end} 章 / 全书 {len(chapters)} 章）')
                return 'err'
            range_desc = _chapter_range_label(start, end, len(chapters))

            download_queue.set_novel_name(novel_id, name)
            socketio.emit('queue_update', download_queue.get_status())

            safe_name = _sanitize_filename(name)
            json_path = os.path.join(BOOKSTORE_DIR, f'{novel_id}_{safe_name}.json')
            txt_path = os.path.join(DOWNLOADS_DIR, f'{safe_name}.txt')
            self.book_json_path = json_path

            novel_content = self._load_progress(json_path)
            if novel_content:
                self.log_callback(
                    f'断点续传《{name}》（{range_desc}）：'
                    f'已有 {len(novel_content)}/{len(chapters_scope)} 章'
                )
            else:
                self.log_callback(
                    f'\n开始下载《{name}》（{range_desc} / 全书 {len(chapters)} 章），'
                    f'状态：{status[0]}'
                )

            chapter_list = list(chapters_scope.items())
            total_chapters = len(chapter_list)
            done_count = sum(
                1 for title, _ in chapter_list
                if (novel_content.get(title) or novel_content.get(title.strip()))
            )

            for title, chapter_id in chapter_list:
                download_queue.wait_if_paused()
                if download_queue.is_cancelled():
                    self._save_progress_files(
                        novel_id, name, chapters, chapters_scope, novel_content,
                        json_path, txt_path, status='cancelled',
                    )
                    self.log_callback(f'下载已中断《{name}》，进度已保存，可稍后继续')
                    return 'cancelled'

                key = title.strip()
                if novel_content.get(title) or novel_content.get(key):
                    continue

                try:
                    content = self._download_chapter(title, chapter_id, novel_content)
                except Exception as e:
                    self.log_callback(f'下载章节失败 {title}: {str(e)}')
                    content = None

                if content:
                    novel_content[title] = content
                    done_count += 1
                    self.progress_callback(
                        done_count, total_chapters, '下载进度', title,
                    )
                    if done_count % 3 == 0:
                        self._save_progress_files(
                            novel_id, name, chapters, chapters_scope, novel_content,
                            json_path, txt_path, status='downloading',
                        )

            if download_queue.is_cancelled():
                self._save_progress_files(
                    novel_id, name, chapters, chapters_scope, novel_content,
                    json_path, txt_path, status='cancelled',
                )
                return 'cancelled'

            self.log_callback('正在校验章节完整性…')
            verified_content = verify_and_fix_chapters(
                novel_id, name, chapters_scope, novel_content, self,
            )
            self._save_progress_files(
                novel_id, name, chapters, chapters_scope, verified_content,
                json_path, txt_path, status='completed',
            )
            self.log_callback(f'《{name}》下载完成：{txt_path}')
            return 's'

        except Exception as e:
            self.log_callback(f'下载失败: {str(e)}')
            return 'err'

# Cookie 状态（Web 先启动，后台再初始化 Cookie）
cookie_state = {
    'ready': False,
    'initializing': True,
    'error': None,
    'message': '正在后台初始化 Cookie…',
}

# 创建下载器实例（defer_cookie：不阻塞 Web 端口启动）
downloader = NovelDownloaderWrapper(
    config=config,
    defer_cookie=True,
    progress_callback=lambda current, total, desc='', chapter='': socketio.emit('progress', {
        'current': current,
        'total': total,
        'percentage': round((current / total * 100) if total > 0 else 0, 2),
        'description': desc or '下载进度',
        'chapter': chapter,
        'text': f'已下载: {current}/{total} 章节 ({round((current / total * 100) if total > 0 else 0, 2)}%)'
    }),
    log_callback=lambda msg: socketio.emit('log', {'message': msg})
)


def _cookie_status_payload() -> dict:
    ready = cookie_state['ready'] or getattr(downloader, 'cookie_ready', False)
    cookie_path = os.path.join(DATA_DIR, 'cookie.json')
    return {
        'ready': ready,
        'initializing': cookie_state.get('initializing', False) and not ready,
        'error': cookie_state['error'],
        'message': cookie_state['message'],
        'has_file': os.path.isfile(cookie_path),
        'hint': '粘贴书籍目录页链接（/page/ID）即可下载；首次下载会自动打开浏览器登录',
        'cookie_file': cookie_path,
    }


def _refresh_cookie_state(emit_update: bool = False) -> None:
    """Derive cookie_state from cookie file and downloader memory state."""
    global cookie_state
    cookie_path = os.path.join(DATA_DIR, 'cookie.json')
    has_file = os.path.isfile(cookie_path)
    stored = None
    if has_file:
        try:
            stored = downloader._load_stored_cookie()
        except Exception as e:
            logger.debug('Could not read cookie file: %s', e)

    memory_ready = bool(getattr(downloader, 'cookie_ready', False))

    if memory_ready and stored:
        cookie_state['ready'] = True
        cookie_state['error'] = None
        cookie_state['message'] = 'Cookie 已就绪，可以下载'
    elif has_file and stored and downloader._is_full_browser_cookie(stored):
        cookie_state['ready'] = False
        cookie_state['error'] = None
        cookie_state['message'] = 'Cookie 已保存，开始下载时自动加载'
    elif has_file and stored:
        cookie_state['ready'] = False
        cookie_state['error'] = None
        cookie_state['message'] = 'Cookie 可能已过期，下载时将自动打开浏览器重新登录'
    else:
        cookie_state['ready'] = False
        if not cookie_state.get('error'):
            cookie_state['error'] = None
            cookie_state['message'] = '未登录，首次下载时将自动打开浏览器登录'

    if emit_update:
        socketio.emit('cookie_update', _cookie_status_payload())


def _background_cookie_init():
    global cookie_state
    cookie_state['initializing'] = True
    cookie_state['message'] = '正在检查 Cookie…'
    try:
        stored = downloader._load_stored_cookie()
        if stored and downloader._is_full_browser_cookie(stored):
            downloader.cookie = stored
            downloader.cookie_ready = True
        _refresh_cookie_state()
    except Exception as e:
        cookie_state['ready'] = False
        cookie_state['error'] = str(e)
        cookie_state['message'] = str(e)
        logger.error('Cookie check failed: %s', e)
    finally:
        cookie_state['initializing'] = False
        socketio.emit('cookie_update', _cookie_status_payload())


threading.Thread(target=_background_cookie_init, daemon=True).start()


class DownloadQueue:
    def __init__(self):
        self.queue = deque()
        self.lock = threading.Lock()
        self.current_download = None
        self.downloading_ids = set()
        self.completed_ids = set()
        self.novel_names = {}
        self.paused = False
        self.cancel_requested = False
        self.chapter_ranges = {}

    def set_novel_name(self, novel_id, name: str):
        if name and name != 'err':
            self.novel_names[str(novel_id)] = name

    def _item(self, novel_id):
        novel_id = str(novel_id)
        return {
            'id': novel_id,
            'name': self.novel_names.get(novel_id, novel_id),
        }

    def add(self, novel_id, name: str = None, chapter_start=None, chapter_end=None):
        with self.lock:
            novel_id = str(novel_id)
            if name:
                self.set_novel_name(novel_id, name)
            if chapter_start is not None or chapter_end is not None:
                start = int(chapter_start if chapter_start is not None else config.chapter_start)
                end = int(chapter_end if chapter_end is not None else config.chapter_end)
                self.chapter_ranges[novel_id] = (start, end)
            if (novel_id not in self.queue and
                    novel_id not in self.downloading_ids):
                self.queue.append(novel_id)
                logger.info(f"Added novel ID {novel_id} to download queue")
            else:
                logger.info(f"Novel ID {novel_id} is already in queue or downloading")

    def take_range(self, novel_id):
        with self.lock:
            return self.chapter_ranges.pop(str(novel_id), None)

    def get_next(self):
        with self.lock:
            if self.queue:
                next_id = self.queue.popleft()
                self.downloading_ids.add(next_id)
                self.cancel_requested = False
                return next_id
            return None

    def finish_download(self, novel_id):
        with self.lock:
            novel_id = str(novel_id)
            if novel_id in self.downloading_ids:
                self.downloading_ids.remove(novel_id)
            if not self.cancel_requested:
                self.completed_ids.add(novel_id)
            else:
                self.cancel_requested = False
            logger.info(f"Finished downloading novel ID {novel_id}")

    def pause(self):
        with self.lock:
            self.paused = True

    def resume(self):
        with self.lock:
            self.paused = False

    def cancel(self):
        with self.lock:
            self.cancel_requested = True
            self.paused = False
            self.queue.clear()

    def clear_all(self):
        """Cancel current task and remove all queued items."""
        with self.lock:
            self.cancel_requested = True
            self.paused = False
            self.queue.clear()
            self.completed_ids.clear()

    def wait_if_paused(self):
        while True:
            with self.lock:
                if not self.paused:
                    return
                cancelled = self.cancel_requested
            if cancelled:
                return
            time.sleep(0.3)

    def is_cancelled(self) -> bool:
        with self.lock:
            return self.cancel_requested

    def get_status(self):
        with self.lock:
            current = self.current_download
            return {
                'queue_length': len(self.queue),
                'current_download': self._item(current) if current else None,
                'queue_items': [self._item(nid) for nid in self.queue],
                'downloading': [self._item(nid) for nid in self.downloading_ids],
                'completed': [self._item(nid) for nid in self.completed_ids],
                'paused': self.paused,
                'cancel_requested': self.cancel_requested,
            }

    def clear_completed(self):
        with self.lock:
            self.completed_ids.clear()

# 创建全局下载队列实例
download_queue = DownloadQueue()

# 创建一个定时器来清理完成的下载记录
def clear_completed_downloads():
    while True:
        time.sleep(300)  # 每5分钟清理一次
        download_queue.clear_completed()

# 启动清理线程
threading.Thread(target=clear_completed_downloads, daemon=True).start()

def _chapter_range(chapters: dict, start: int, end: int) -> dict:
    """Return chapters[start..end] (1-based, end inclusive). end<=0 means through last."""
    items = list(chapters.items())
    total = len(items)
    if total == 0:
        return {}
    start = max(1, int(start or 1))
    end = int(end if end is not None else 0)
    if end <= 0:
        end = total
    else:
        end = min(end, total)
    if start > end:
        return {}
    return dict(items[start - 1:end])


def _chapter_range_label(start: int, end: int, total: int) -> str:
    start = max(1, int(start or 1))
    end = int(end if end is not None else 0)
    if end <= 0:
        if total > 0 and start == 1:
            return f'全书 {total} 章'
        if total > 0:
            return f'第 {start}–{total} 章'
        return f'第 {start} 章起至末尾'
    if total > 0:
        end_disp = min(end, total)
        if start == 1 and end_disp >= total:
            return f'全书 {total} 章'
        return f'第 {start}–{end_disp} 章'
    return f'第 {start}–{end} 章'


def _resolve_download_range(novel_id) -> tuple[int, int]:
    """Per-task override from queue, else live config (no restart needed)."""
    override = download_queue.take_range(novel_id)
    if override:
        return override
    return (
        int(getattr(config, 'chapter_start', 1) or 1),
        int(getattr(config, 'chapter_end', 10)),
    )


def _apply_chapter_range_settings(data: dict) -> None:
    """Update chapter range on config from API payload."""
    if 'chapter_start' in data or 'chapter_end' in data:
        config.chapter_start = max(1, int(data.get('chapter_start', config.chapter_start)))
        config.chapter_end = int(data.get('chapter_end', config.chapter_end))
    elif 'max_chapters' in data:
        mc = int(data['max_chapters'])
        config.chapter_start = 1
        config.chapter_end = mc


def _resolve_novel_name(novel_id: str) -> str:
    """Resolve novel title from local cache or API."""
    novel_id = str(novel_id)
    if os.path.exists(BOOKSTORE_DIR):
        for file in os.listdir(BOOKSTORE_DIR):
            if file.startswith(f'{novel_id}_') and file.endswith('.json'):
                return os.path.splitext(file.split('_', 1)[1])[0]
    try:
        name, _, _ = downloader._get_chapter_list(novel_id)
        if name != 'err':
            return name
    except Exception as e:
        logger.debug('Could not resolve novel name for %s: %s', novel_id, e)
    return novel_id


def _delete_novel_files(novel_id: str) -> list[str]:
    """Delete bookstore JSON and downloaded TXT for a novel."""
    novel_id = str(novel_id)
    removed: list[str] = []
    if not os.path.exists(BOOKSTORE_DIR):
        return removed
    for file in os.listdir(BOOKSTORE_DIR):
        if not (file.startswith(f'{novel_id}_') and file.endswith('.json')):
            continue
        json_path = os.path.join(BOOKSTORE_DIR, file)
        os.remove(json_path)
        removed.append(file)
        novel_name = os.path.splitext(file.split('_', 1)[1])[0]
        txt_path = os.path.join(DOWNLOADS_DIR, f'{novel_name}.txt')
        if os.path.exists(txt_path):
            os.remove(txt_path)
            removed.append(os.path.basename(txt_path))
    return removed


def load_config():
    """Load saved configuration"""
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r', encoding='UTF-8') as f:
                saved_config = json.load(f)

                config.delay = saved_config.get('delay', config.delay)
                config.save_path = saved_config.get('save_path', DOWNLOADS_DIR)
                config.xc = 1
                if 'chapter_start' in saved_config or 'chapter_end' in saved_config:
                    config.chapter_start = max(1, int(saved_config.get('chapter_start', 1)))
                    config.chapter_end = int(saved_config.get('chapter_end', 10))
                else:
                    mc = int(saved_config.get('max_chapters', config.chapter_end))
                    config.chapter_start = 1
                    config.chapter_end = mc if mc > 0 else 0

                logger.info("Configuration loaded successfully")
    except Exception as e:
        logger.error(f"Error loading configuration: {str(e)}")

def save_config():
    """Save current configuration"""
    try:
        config_data = {
            'delay': config.delay,
            'save_path': config.save_path,
            'xc': config.xc,
            'chapter_start': config.chapter_start,
            'chapter_end': config.chapter_end,
        }
        
        with open(CONFIG_FILE, 'w', encoding='UTF-8') as f:
            json.dump(config_data, f, ensure_ascii=False, indent=4)
            
        logger.info("Configuration saved successfully")
    except Exception as e:
        logger.error(f"Error saving configuration: {str(e)}")

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/cookie/status')
def cookie_status():
    if not cookie_state.get('initializing'):
        _refresh_cookie_state()
    return jsonify(_cookie_status_payload())


@app.route('/api/cookie/login', methods=['POST'])
def cookie_login():
    """Open browser for fanqienovel login and save cookie."""
    global cookie_state
    cookie_state['initializing'] = True
    cookie_state['message'] = '正在打开浏览器…'
    try:
        from cookie_browser import login_and_save_cookie
        cookie_path = os.path.join(DATA_DIR, 'cookie.json')
        profile = os.path.join(DATA_DIR, 'browser_profile')

        def _log(msg):
            socketio.emit('log', {'message': msg})

        login_and_save_cookie(cookie_path, profile_dir=profile, log=_log)
        downloader.cookie = ''
        downloader.cookie_ready = False
        downloader.ensure_cookie_ready(browser_profile_dir=profile)
        return jsonify({'status': 'success', 'message': cookie_state['message']})
    except Exception as e:
        cookie_state['ready'] = False
        cookie_state['error'] = str(e)
        cookie_state['message'] = str(e)
        logger.exception('Browser login failed')
        return jsonify({'error': str(e)}), 500
    finally:
        cookie_state['initializing'] = False
        if not cookie_state.get('error'):
            _refresh_cookie_state(emit_update=True)
        else:
            socketio.emit('cookie_update', _cookie_status_payload())


@app.route('/api/cookie/clear', methods=['POST'])
def cookie_clear():
    """Remove saved cookie and reset downloader login state."""
    global cookie_state
    removed = []
    for name in ('cookie.json', 'cookie_raw.txt'):
        path = os.path.join(DATA_DIR, name)
        if os.path.exists(path):
            os.remove(path)
            removed.append(name)

    downloader.cookie = ''
    downloader.cookie_ready = False
    cookie_state['initializing'] = False
    cookie_state['error'] = None
    cookie_state['ready'] = False
    cookie_state['message'] = 'Cookie 已清除，下次下载时将重新打开浏览器登录'
    socketio.emit('cookie_update', _cookie_status_payload())

    return jsonify({
        'status': 'success',
        'message': cookie_state['message'],
        'removed': removed,
    })


@app.route('/api/novels')
def list_novels():
    """List downloaded novels with their status"""
    try:
        novels = []
        if os.path.exists(BOOKSTORE_DIR):
            for file in os.listdir(BOOKSTORE_DIR):
                if file.endswith('.json'):
                    try:
                        file_path = os.path.join(BOOKSTORE_DIR, file)
                        last_modified = os.path.getmtime(file_path)
                        last_modified_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(last_modified))
                        
                        # 从文件名中提取小说ID和名称
                        parts = file.split('_', 1)  # 分割一次，获取ID和名称
                        novel_id = parts[0]
                        novel_name = os.path.splitext(parts[1])[0] if len(parts) > 1 else os.path.splitext(file)[0]
                        
                        # 读取JSON文件获取章节数量
                        with open(file_path, 'r', encoding='UTF-8') as f:
                            data = json.load(f)
                            chapter_count = len(data.get('chapters', {}))
                            meta = data.get('_meta', {})
                            
                        novels.append({
                            'name': novel_name,
                            'status': f'已下载 {chapter_count} 章',
                            'last_updated': last_modified_str,
                            'novel_id': novel_id
                        })
                    except Exception as e:
                        logger.error(f"Error processing file {file}: {str(e)}")
                        continue
        
        return jsonify(novels)
    except Exception as e:
        logger.error(f"Error listing novels: {str(e)}")
        return jsonify([])


def handle_errors(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except Exception as e:
            logger.error(f"Error in {f.__name__}: {str(e)}")
            logger.exception("Full traceback:")
            return jsonify({'error': str(e)}), 500
    return wrapper


@app.route('/api/novels/<novel_id>', methods=['DELETE'])
@handle_errors
def delete_novel(novel_id):
    """Remove a novel from library (JSON + TXT)."""
    removed = _delete_novel_files(novel_id)
    if not removed:
        return jsonify({'error': '未找到该小说'}), 404
    return jsonify({'status': 'success', 'deleted': removed})

# 优化路由处理：仅加入队列（TXT + 断点续传 + 暂停/取消）
@app.route('/api/download/<novel_id>', methods=['GET', 'POST'])
@handle_errors
def download_novel(novel_id):
    """Add a novel to the download queue."""
    novel_id = str(novel_id).strip()
    if not novel_id.isdigit():
        return jsonify({'error': '无效的书籍 ID'}), 400

    try:
        name, _, _ = downloader._get_chapter_list(novel_id)
        if name == 'err':
            return jsonify({
                'error': 'Novel not found',
                'hint': (
                    '请使用书籍目录页链接（/page/书籍ID），'
                    '不要用章节阅读页（/reader/章节ID）。'
                    '在阅读页点击书名进入目录后复制地址栏链接。'
                ),
            }), 404
        novel_name = name
    except CookieInitError:
        novel_name = _resolve_novel_name(novel_id)

    data = request.get_json(silent=True) or {}
    cs = data.get('chapter_start')
    ce = data.get('chapter_end')
    if cs is not None or ce is not None:
        start = max(1, int(cs if cs is not None else config.chapter_start))
        end = int(ce if ce is not None else config.chapter_end)
    else:
        start, end = config.chapter_start, config.chapter_end

    download_queue.add(
        novel_id,
        novel_name,
        chapter_start=start,
        chapter_end=end,
    )
    socketio.emit('queue_update', download_queue.get_status())
    socketio.emit('show_progress', {})
    return jsonify({
        'status': 'queued',
        'novel_id': novel_id,
        'message': (
            f'已加入下载队列（{_chapter_range_label(start, end, 0)}），'
            '支持暂停与断点续传'
        ),
    })

@app.route('/api/settings', methods=['GET', 'POST'])
def settings():
    if request.method == 'POST':
        try:
            data = request.json
            config.delay = data.get('delay', config.delay)
            config.xc = 1
            _apply_chapter_range_settings(data)
            
            # 保存设置到文件
            save_config()
            
            return jsonify({'status': 'success'})
        except Exception as e:
            logger.error(f"Error saving settings: {str(e)}")
            return jsonify({'error': str(e)}), 500
            
    return jsonify({
        'delay': config.delay,
        'xc': config.xc,
        'chapter_start': config.chapter_start,
        'chapter_end': config.chapter_end,
    })

@app.route('/download/<path:filename>')
def download_file(filename):
    """Download a novel TXT file."""
    filepath = os.path.join(DOWNLOADS_DIR, filename)
    if os.path.exists(filepath):
        return send_file(filepath, as_attachment=True)
    return jsonify({'error': 'File not found'}), 404

@app.route('/components/<template>')
def get_component(template):
    """Serve component templates"""
    try:
        # 确保template参数包含.html扩展名
        if not template.endswith('.html'):
            template += '.html'
        app.logger.info(f"Loading template: components/{template}")
        
        # 如果是阅读器页面，返回完整的HTML
        if template == 'reader.html':
            return render_template(f'components/{template}')
        
        # 对于其他组件，返回片段
        return render_template(f'components/{template}', layout=False)
        
    except Exception as e:
        app.logger.error(f"Error loading template {template}: {str(e)}")
        return f"Error loading template: {str(e)}", 404

@app.route('/api/update-all', methods=['POST'])
def update_all():
    """Update all novels in the library"""
    try:
        update_count = 0
        if os.path.exists(BOOKSTORE_DIR):
            for file in os.listdir(BOOKSTORE_DIR):
                if not file.endswith('.json'):
                    continue
                parts = file.split('_', 1)
                novel_id = parts[0]
                name = os.path.splitext(parts[1])[0] if len(parts) > 1 else novel_id
                download_queue.add(novel_id, name)
                update_count += 1

        if update_count > 0:
            socketio.emit('log', {'message': f'已添加 {update_count} 小说到更新队列'})
            return jsonify({'status': 'queued', 'count': update_count})
        socketio.emit('log', {'message': '没有找到可以更新的小说'})
        return jsonify({'status': 'no_novels'})
            
    except Exception as e:
        error_msg = f'更新失败: {str(e)}'
        socketio.emit('log', {'message': error_msg})
        return jsonify({'error': error_msg}), 500

@app.route('/api/queue/status')
def get_queue_status():
    return jsonify(download_queue.get_status())

@app.route('/api/queue/add/<novel_id>', methods=['POST'])
def add_to_queue(novel_id):
    name = _resolve_novel_name(novel_id)
    download_queue.add(novel_id, name)
    socketio.emit('queue_update', download_queue.get_status())
    socketio.emit('show_progress', {})
    return jsonify({'status': 'success'})


@app.route('/api/queue/pause', methods=['POST'])
def queue_pause():
    download_queue.pause()
    socketio.emit('queue_update', download_queue.get_status())
    socketio.emit('log', {'message': '下载已暂停'})
    return jsonify({'status': 'paused'})


@app.route('/api/queue/resume', methods=['POST'])
def queue_resume():
    download_queue.resume()
    socketio.emit('queue_update', download_queue.get_status())
    socketio.emit('log', {'message': '下载已继续'})
    return jsonify({'status': 'resumed'})


@app.route('/api/queue/cancel', methods=['POST'])
def queue_cancel():
    download_queue.cancel()
    socketio.emit('queue_update', download_queue.get_status())
    socketio.emit('log', {'message': '下载已取消，进度已保存，可重新下载以断点续传'})
    return jsonify({'status': 'cancelled'})


@app.route('/api/queue/clear', methods=['POST'])
def queue_clear():
    download_queue.clear_all()
    socketio.emit('queue_update', download_queue.get_status())
    socketio.emit('log', {'message': '下载队列已清空'})
    return jsonify({'status': 'cleared'})

def _sanitize_filename(filename: str) -> str:
    """Sanitize filename by removing invalid characters"""
    # Windows不允许的字符: \ / : * ? " < > |
    # 只替换这些Windows不支持的字符，保留中文标点
    filename = filename.strip()
    
    # 只替换Windows不允许的字符和一些特殊字符
    replacements = {
        '/': '／',  # 使用全角斜杠
        '\\': '＼', # 使用全角反斜杠
        ':': '：',  # 使用中文冒号
        '*': '＊',  # 使用全角星号
        '?': '？',  # 使用中文问号
        '"': '"',   # 使用中文引号
        '<': '＜',  # 使用全角小于号
        '>': '＞',  # 使用全角大于号
        '|': '｜',  # 使用全角竖线
        '\n': '',   # 移除换行符
        '\r': '',   # 移除回车符
        '\t': ' ',  # 制表符替换为空格
    }
    
    for old, new in replacements.items():
        filename = filename.replace(old, new)
    
    # 移除开头和结尾的空格
    filename = filename.strip()
    
    # 确保文件名不为空
    if not filename:
        filename = 'untitled'
        
    # 限制文件名长度
    if len(filename) > 100:
        filename = filename[:100]
        
    return filename

@app.route('/api/read/<novel_id>/<chapter_title>')
def read_chapter(novel_id, chapter_title):
    """API endpoint to read a specific chapter of a novel"""
    try:
        logger.info(f"Attempting to read chapter: {chapter_title} from novel: {novel_id}")
        
        # 首先确保小说已下载
        if not os.path.exists(BOOKSTORE_DIR):
            logger.error(f"Bookstore directory not found: {BOOKSTORE_DIR}")
            os.makedirs(BOOKSTORE_DIR, exist_ok=True)
            
        # 获取小说信息
        name, chapters, _ = downloader._get_chapter_list(novel_id)
        if name == 'err':
            return jsonify({
                'error': 'Novel not found',
                'hint': (
                    '请使用书籍目录页链接（/page/书籍ID），'
                    '不要用章节阅读页（/reader/章节ID）。'
                    '在阅读页点击书名进入目录后复制地址栏链接。'
                ),
            }), 404
            
        # 处理文件名，确保一致性
        safe_name = _sanitize_filename(name)
        json_path = os.path.join(BOOKSTORE_DIR, f'{novel_id}_{safe_name}.json')  # 使用带ID的文件名
        logger.info(f"Looking for JSON file at: {json_path}")
        
        # 如果文件不存在，等待下载完成
        if not os.path.exists(json_path):
            # 检查是否已经在下载
            if novel_id not in download_queue.downloading_ids and novel_id not in download_queue.queue:
                logger.info(f"Novel file not found, adding to download queue: {novel_id}")
                download_queue.add(novel_id)
                socketio.emit('queue_update', download_queue.get_status())
            
            # 等待下载完成
            while novel_id in download_queue.downloading_ids or novel_id in download_queue.queue:
                time.sleep(0.5)
            
            # 再次检查文件是否存在
            if not os.path.exists(json_path):
                logger.error(f"JSON file still not found after download: {json_path}")
                return jsonify({'error': 'Failed to create novel file'}), 500
        
        # 读取JSON文件
        try:
            with open(json_path, 'r', encoding='UTF-8') as f:
                novel_data = json.load(f)
                # 从 novel_data 中获取章节内容
                chapters_data = novel_data.get('chapters', {})
                chapter_content = chapters_data.get(chapter_title)
                
                if chapter_content is None:
                    logger.error(f"Chapter not found: {chapter_title}")
                    return jsonify({'error': 'Chapter not found'}), 404
                    
                return jsonify({
                    'title': chapter_title,
                    'content': chapter_content
                })
                
        except Exception as e:
            logger.error(f"Error reading JSON file: {str(e)}")
            return jsonify({'error': 'Failed to read novel data'}), 500

    except Exception as e:
        logger.error(f"Error reading chapter: {str(e)}")
        logger.exception("Full traceback:")
        return jsonify({'error': str(e)}), 500

def process_download_queue():
    profile_dir = os.path.join(DATA_DIR, 'browser_profile')
    while True:
        novel_id = download_queue.get_next()
        if novel_id:
            download_queue.set_novel_name(novel_id, _resolve_novel_name(novel_id))
            download_queue.current_download = novel_id
            socketio.emit('queue_update', download_queue.get_status())
            try:
                with app.app_context():
                    try:
                        downloader.ensure_cookie_ready(browser_profile_dir=profile_dir)
                    except CookieInitError as e:
                        socketio.emit('log', {'message': str(e)})
                    else:
                        result = downloader.download_novel(novel_id)
                        if result == 's':
                            socketio.emit('log', {
                                'message': f'小说 {novel_id} 下载完成（TXT）',
                            })
                        elif result == 'cancelled':
                            socketio.emit('log', {
                                'message': f'小说 {novel_id} 已中断，进度已保存',
                            })
                        else:
                            socketio.emit('log', {
                                'message': f'小说 {novel_id} 下载失败',
                            })
            except Exception as e:
                socketio.emit('log', {'message': f'下载失败: {str(e)}'})
            finally:
                download_queue.current_download = None
                download_queue.finish_download(novel_id)
                socketio.emit('queue_update', download_queue.get_status())
        time.sleep(1)

# 启动队列处理线程
threading.Thread(target=process_download_queue, daemon=True).start()

def print_server_info():
    """Print server access information"""
    curl_hint = ''
    from main import HAS_CURL_CFFI
    if not HAS_CURL_CFFI:
        curl_hint = '''
│  ⚠ 未检测到 curl_cffi，极易触发验证码！                      │
│     请先 Ctrl+C 停止服务，再执行:                            │
│     pip install curl_cffi                                    │
│     然后重新 python server.py                                │
'''
    logger.info(f"""
────────────────────────────────────────────────
│                                                  │
│   番茄小说下载器 Web 服务已启动                      │
│                                                  │
│   请在浏览器中访问:                                 │
│   http://localhost:12930                         │
{curl_hint}│                                                  │
╰──────────────────────────────────────────────────╯
    """)

@app.route('/api/chapters/<novel_id>')
@handle_errors
def get_chapters(novel_id):
    """Get chapter list for a novel"""
    try:
        name, chapters, status = downloader._get_chapter_list(novel_id)
        if name == 'err':
            raise Exception('Novel not found')
            
        # 将章节信息转换为列表格式并排序
        chapter_list = []
        for title, chapter_id in chapters.items():
            try:
                # 提取章节号，支持多种格式
                match = re.search(r'(?:第|^)(\d+)(?:章|节|集|卷|部|篇|回|话)', title)
                if match:
                    chapter_num = int(match.group(1))
                else:
                    # 尝试直接提取数字
                    numbers = re.findall(r'\d+', title)
                    chapter_num = int(numbers[0]) if numbers else float('inf')
            except:
                chapter_num = float('inf')
                
            chapter_list.append({
                'title': title,
                'id': chapter_id,
                'chapter_num': chapter_num,
                'original_index': len(chapter_list)
            })
        
        # 首先按章节号排序
        chapter_list.sort(key=lambda x: x['chapter_num'])
        
        # 对于章节号相同的情况，按原始顺序排序
        chapter_list.sort(key=lambda x: (x['chapter_num'], x['original_index']))
        
        # 移除辅助字段
        for chapter in chapter_list:
            del chapter['original_index']
            del chapter['chapter_num']
        
        return jsonify({
            'name': name,
            'chapters': chapter_list,
            'status': status[0] if status else None
        })
    except Exception as e:
        logger.error(f"Error getting chapters: {str(e)}")
        logger.exception("Full traceback:")
        return jsonify({'error': str(e)}), 500

# 添加更详细的错误日志
@app.errorhandler(Exception)
def handle_error(error):
    logger.error(f"Unhandled error: {str(error)}")
    logger.exception("Full traceback:")
    return jsonify({
        'error': str(error),
        'details': traceback.format_exc()
    }), 500

def check_chapter_content(content: str) -> bool:
    """检查章节内容是否完整有效"""
    if not content:
        return False
    # 检查内容是否太短（可能是下载失败）
    if len(content) < 100:  # 假设正常章节至少有100个字符
        return False
    # 检查是否包含常见的错误标记
    error_markers = ['下载失败', '获取失败', '请求失败', '访问太频繁']
    return not any(marker in content for marker in error_markers)

def verify_and_fix_chapters(novel_id: str, name: str, chapters: dict, novel_content: dict, downloader) -> dict:
    """验证章节完整性并修复缺失或损坏的章节"""
    logger.info(f"开始验证章节完整性: {name}")
    fixed_content = novel_content.copy()
    failed_chapters = []
    
    # 检查每个章节
    for title, chapter_id in chapters.items():
        content = novel_content.get(title)
        if not content or not check_chapter_content(content):
            logger.warning(f"发现问题章节: {title}")
            failed_chapters.append((title, chapter_id))
            
    # 如果有问题章节，尝试重新下载
    if failed_chapters:
        logger.info(f"发现 {len(failed_chapters)} 个问题章节，开始修复")
        max_retries = 3
        retry_count = 0
        
        while failed_chapters and retry_count < max_retries:
            retry_count += 1
            logger.info(f"第 {retry_count} 次尝试修复")
            
            still_failed = []
            for title, chapter_id in failed_chapters:
                try:
                    logger.info(f"重新下载章节: {title}")
                    content = downloader._download_chapter(title, chapter_id, {})
                    if content and check_chapter_content(content):
                        fixed_content[title] = content
                        logger.info(f"成功修复章节: {title}")
                    else:
                        still_failed.append((title, chapter_id))
                except Exception as e:
                    logger.error(f"修复章节失败 {title}: {str(e)}")
                    still_failed.append((title, chapter_id))
                
                # 添加延迟避免请求过快
                time.sleep(random.randint(
                    max(downloader.config.delay[0], 3000),
                    max(downloader.config.delay[1], 6000),
                ) / 1000)
            
            failed_chapters = still_failed
            
            if failed_chapters:
                logger.warning(f"仍有 {len(failed_chapters)} 个章节修复失败，等待后重试")
                time.sleep(5)  # 较长的等待时间
    
    # 保存修复后的内容
    if failed_chapters:
        logger.warning(f"最终仍有 {len(failed_chapters)} 个章节未能修复")
        # 记录未修复的章节信息
        fixed_content['_failed_chapters'] = [title for title, _ in failed_chapters]
    else:
        logger.info("所有章节验证完成，内容完整")
        
    return fixed_content


def _open_browser_when_ready(port: int) -> None:
    """Open default browser once the local server accepts connections."""
    if os.environ.get('FANQIE_OPEN_BROWSER', '1').strip().lower() in ('0', 'false', 'no'):
        return
    url = f'http://127.0.0.1:{port}/'
    for _ in range(120):
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=0.5):
                webbrowser.open(url)
                logger.info('已自动打开浏览器: %s', url)
                return
        except OSError:
            time.sleep(0.25)
    logger.warning('未能自动打开浏览器，请手动访问 %s', url)


if __name__ == '__main__':
    load_config()
    config.xc = 1

    app.debug = False
    print_server_info()

    port = int(os.environ.get('FANQIE_PORT', '12930'))
    killed = ensure_port_free(port)
    if killed:
        logger.info('已自动释放端口 %s（结束进程: %s）', port, killed)

    threading.Thread(target=_open_browser_when_ready, args=(port,), daemon=True).start()

    socketio.run(
        app,
        host='0.0.0.0',
        port=port,
        debug=False,
        use_reloader=False,
    )
