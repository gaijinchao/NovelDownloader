# 番茄小说下载器

基于 [fanqienovel-downloader](https://github.com/ying-ck/fanqienovel-downloader) 重构并精简的 **Web 专用版**：浏览器界面管理书库，下载番茄小说为 **单个 TXT 文件**。

请勿滥用，且用且珍惜。

## 功能

- 粘贴 `/page/书籍ID` 链接下载
- 书库管理：阅读已下载小说、更新、删除
- 下载队列：批量排队，支持暂停 / 取消 / 清空
- **断点续传**：中断后再次下载同一本书会从上次进度继续
- 在线阅读（点击书库卡片）
- 首次下载自动打开浏览器登录
- 默认下载前 **10** 章（SVIP用户可在网页端下载全部章节）

<img width="2864" height="1536" alt="9eb52a37023a90d45b409ccd816763cc" src="https://github.com/user-attachments/assets/acc85c62-59dc-45c3-afb2-887a19b754ec" />
<img width="2864" height="1536" alt="4ba8422787b6d2a49cf74d691b7a8ad8" src="https://github.com/user-attachments/assets/ab5e8726-ed2f-4cc0-b8fe-21d7b7393490" />
<img width="2864" height="1536" alt="b9e997f0d3278060eb5fbdcefccdb31d" src="https://github.com/user-attachments/assets/2b07cef1-abad-49d2-aeae-ee9351de25c8" />


## 环境要求

- Python 3.9+
- Windows / macOS / Linux

## 安装与启动

### 一键启动（推荐）

1. 安装 [Python 3.9+](https://www.python.org/downloads/)（Windows 安装时勾选 **Add python.exe to PATH**）
2. 解压本项目
3. **双击 `启动.bat`** 或 **`start.bat`**（macOS / Linux 运行 `chmod +x start.sh && ./start.sh`）

脚本会自动：创建虚拟环境 → 安装依赖 → 安装 Playwright → 启动服务 → **打开浏览器**。

若端口 12930 被占用，程序会自动尝试释放。

### 手动启动

```bash
cd fanqienovel-downloader-main
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # macOS / Linux

pip install -r requirements.txt
playwright install chromium

cd src
python server.py
```

浏览器访问：**http://localhost:12930**

## 使用流程

1. 在书库页粘贴书籍**目录页**链接，例如：  
   `https://fanqienovel.com/page/7558397007656668185`
2. 首次下载会弹出浏览器，登录番茄小说账号
3. 下载完成后 TXT 保存在 `src/novel_downloads/`，元数据在 `src/data/bookstore/`

> 请使用 `/page/` 目录页链接，不要使用 `/reader/` 章节阅读页链接。

## 设置说明

在 Web 界面「设置」中可调整：

| 选项 | 说明 |
|------|------|
| 正文段首占位符 | 段首空格字符及数量 |
| 章节下载间隔 | 毫秒，建议 1500–3500，过小易触发验证码 |
| 下载章节数 | 默认 10；`0` 表示下载全书 |

配置保存在 `src/data/web_config.json`。

## Cookie

- **推荐**：首次下载时自动浏览器登录（见上文）
- **手动导入**：见 [`src/COOKIE_SETUP.md`](src/COOKIE_SETUP.md)，或运行 `python setup_cookie.py`

## 目录结构

```
src/
├── server.py           # Web 服务入口
├── main.py             # 下载核心逻辑
├── cookie_browser.py   # 浏览器自动登录
├── templates/          # 页面模板
├── static/             # 前端静态资源
├── data/               # Cookie、书库 JSON、配置
└── novel_downloads/    # 下载的 TXT 文件
```

## 常见问题

### 网络 / 代理错误

若出现 `ProxyError`、`MaxRetryError` 等，请检查网络并**关闭系统代理**或配置正确的代理。

### 频繁验证码

安装并重启服务：

```bash
pip install curl_cffi
```

修改依赖或代码后需重新运行 `python server.py` 才会生效。

### 中文乱码（界面显示问号）

确保 `src/templates/` 下 HTML 文件为 **UTF-8** 编码保存。


## 免责声明

本程序仅供 Python 网络爬虫与网页处理相关的**学习与研究**使用，不得用于任何违法或侵犯他人权利的行为。使用者须自行承担法律责任；作者与贡献者不对使用本程序造成的任何损失负责。

在使用前请遵守相关法律法规及网站使用政策。

This program is for educational and research purposes only. Users are responsible for compliance with applicable laws and website terms of service.

## 开源

本项目基于原项目重构，原程序遵循 [AGPL-3.0](https://github.com/ying-ck/fanqienovel-downloader?tab=AGPL-3.0-1-ov-file) 协议。

## 致谢

原项目作者：Yck ([ying-ck](https://github.com/ying-ck))、Yqy ([qxqycb](https://github.com/qxqycb))、Lingo ([lingo34](https://github.com/lingo34))
