# Cookie 配置指南（下载章节必看）

当前番茄阅读页常会弹出**滑动验证码**。仅自动生成 `novel_web_id` 往往**无法下载正文**，需要你在浏览器登录后导出 **完整 Cookie**（方案 A）。

## 你需要做的事（约 5 分钟）

### 1. 浏览器里拿到 Cookie

1. 用 Chrome / Edge 打开 https://fanqienovel.com  
2. **登录**你的番茄小说账号（有 SVIP 更稳，非必须）  
3. 随便打开一本书的**某一章**，在页面上**完成滑动验证**，确认能正常看到正文  
4. 安装 Cookie 导出插件（任选其一）  
   - [Cookie-Editor](https://chrome.google.com/webstore)  
   - EditThisCookie 等  
5. 在 `fanqienovel.com` 域名下导出 Cookie：  
   - **推荐**：复制为 **Header String**（`name=value; name2=value2; ...`）  
   - 或导出 **JSON** 列表也可  

### 2. 写入本项目

**方式 A（推荐）—— 运行配置脚本**

```powershell
cd 项目目录\src
python setup_cookie.py
```

按提示粘贴 Cookie，看到「阅读页校验通过」即可。

**方式 B —— 手动写文件**

把 Cookie 整行保存为 `src/data/cookie.json`，内容是 **JSON 字符串**（注意外层有引号）：

```json
"novel_web_id=xxx; sessionid=xxx; ttwid=xxx; csrf_session_id=xxx"
```

也可先把 Cookie 贴到 `src/data/cookie_raw.txt`，再运行 `python setup_cookie.py`。

### 3. 启动 Web 版

```powershell
cd src
python -u server.py
```

浏览器打开 http://localhost:12930  

- 页面可先打开（Cookie 在后台初始化）  
- 访问 http://localhost:12930/api/cookie/status 查看是否 `ready: true`  
- 只有 `ready: true` 时下载章节才可靠  

## 环境变量（可选）

- `FANQIE_COOKIE`：完整 Cookie 字符串，优先级高于 `cookie.json`  
- `FANQIE_SKIP_AUTO_COOKIE=1`：禁用自动撞库，只用你提供的 Cookie  

## 常见问题

| 现象 | 处理 |
|------|------|
| 一直「正在获取 Cookie」 | 已改进：Web 会先启动；请用 `setup_cookie.py` 导入浏览器 Cookie |
| 校验失败 / 验证码 | 回浏览器重新登录、过验证，再导出**新** Cookie |
| 代理报错 | 关闭错误系统代理（见 README Q1） |
| Cookie 过期 | 重新导出并再运行 `setup_cookie.py` |

## 我能自动做什么 / 不能做什么

| 自动 | 需你配合 |
|------|----------|
| Web 先监听 12930，后台试 Cookie | 在浏览器登录并过验证码 |
| 读取 `cookie.json` / 环境变量 | 用插件导出完整 Cookie |
| 校验能否读到章节正文 | Cookie 过期后重新导出 |
| 撞库 `novel_web_id`（最多 500 次） | 验证码环境下通常仍下不了章节 |

**结论：想稳定下载章节，请按本文完成方案 A，再启动 `server.py`。**
