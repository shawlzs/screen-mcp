# screen-mcp

一个 MCP (Model Context Protocol) 服务器，让 Claude Agent / Claude Code 可以**按需**截取用户屏幕，并维护一个最近若干帧的滑动窗口作为多模态问答的上下文。

设计灵感来自豆包等工具的"共享屏幕"功能 —— **低频轮询**循环持续维护上下文，加上**按需截图**路径处理一次性提问；并对采集到的画面执行严格的**不落盘、不缓存**策略。

## 功能特性

- **6 个 MCP tool**：`start_capture`、`stop_capture`、`capture_now`、`set_polling`、`list_windows`、`analyze_screen`
- **Anthropic Messages API 兼容视觉后端**：默认走官方 API，也可指向国内大模型代理（见[使用国内模型代理](#使用国内模型代理)）
- **多帧上下文**：基于感知哈希 (perceptual hash) 去重，维护最多 20 帧不同的滑动窗口；`analyze_screen` 把最近 N 帧一起发给视觉模型
- **单 session 不变量**：每个进程最多一个采集会话 —— mss / DXGI 等采集设备无法安全共享
- **Windows 原生单窗口采集**：通过 `PrintWindow + PW_RENDERFULLCONTENT`；全屏采集在所有平台走 [`mss`](https://github.com/BoboTiG/cookiecutter-mss)
- **数据本地化**：画面在内存里 WebP 编码后经 TLS 发给视觉 API，**不写盘、不缓存**

## 平台支持

| 平台    | `fullscreen` | `window`（单应用）        |
|---------|:------------:|:-------------------------:|
| Linux   | ✅ (`mss`)   | ❌ 返回 `unsupported_platform` |
| macOS   | ✅ (`mss`)   | ❌ 返回 `unsupported_platform` |
| Windows | ✅ (`mss`)   | ✅ (`PrintWindow`)         |

MCP 传输层（stdio）在所有平台都能跑；只有 `mode='window'` 采集路径是 Windows 专属。

## 安装

推荐用 [`uv`](https://github.com/astral-sh/uv) 创建虚拟环境并安装，避免污染全局 Python。

```bash
# 创建虚拟环境（如果还没有）
uv venv .venv

# 以 editable 模式安装（含 dev 和 windows 依赖）
uv pip install -e ".[dev,windows]"
```

传统 `pip` 也可以（前提是已经在虚拟环境里）：

```bash
# Linux / macOS（开发环境）
pip install -e ".[dev]"

# Windows（目标运行时，会额外装 pywin32）
pip install -e ".[dev,windows]"
```

第一次运行会读取 `.env` 文件 —— 见 [配置](#配置) 章节。

## 配置

把 `.env.example` 复制成 `.env`，填入 Anthropic API key：

```bash
cp .env.example .env
# 编辑 .env，设置 ANTHROPIC_API_KEY
```

所有可配置项（含默认值）：

| 环境变量                  | 默认值               | 用途                                              |
|---------------------------|----------------------|---------------------------------------------------|
| `VISION_PROVIDER`         | `anthropic`          | 目前只实现了 `anthropic`（兼容所有 Anthropic Messages API 端点） |
| `ANTHROPIC_API_KEY`       | *(必填)*             | API 密钥                                          |
| `ANTHROPIC_MODEL`         | *(必填)*             | 调用的模型名（官方 API 用 `claude-sonnet-4-6`，代理用代理方指定的字符串）|
| `ANTHROPIC_BASE_URL`      | *(空)*               | 留空走官方 API；填了就走该 URL 下的 `/v1/messages` |
| `DEFAULT_POLLING_INTERVAL`| `3.0`                | 轮询模式下相邻两次采集的间隔（秒）                |
| `MAX_FRAME_BUFFER`        | `20`                 | 滑动窗口大小                                      |
| `PHASH_DEDUPE_THRESHOLD`  | `6`                  | 汉明距阈值，低于此值视为重复帧丢弃                |
| `PHASH_DEDUPE_LOOKBACK`   | `3`                  | 与最近多少帧做 phash 比较                         |
| `WEBP_QUALITY`            | `75`                 | 存储帧的 WebP 压缩质量（1-100）                   |
| `CAPTURE_MAX_EDGE`        | `1564`               | 发给视觉 API 前的长边像素上限（Anthropic 推荐值） |

> ⚠️ 如果你的 shell 已经导出了 `ANTHROPIC_MODEL`（比如给 Claude Code 用的），把这个值复制到 `.env` 里，让本项目的模型选择显式可见。

### 使用国内模型代理

如果你的 Claude Code 走的是国产大模型代理（代理对外暴露 Anthropic Messages API，即 `/v1/messages`），直接在 `.env` 里把 `ANTHROPIC_BASE_URL` 指向代理地址：

```bash
# .env
ANTHROPIC_BASE_URL=https://your-proxy.example.com/anthropic
ANTHROPIC_MODEL=your-model-name          # 代理方指定的模型字符串
ANTHROPIC_API_KEY=your-proxy-key
```

`vision/anthropic.py` 用的是官方 `anthropic` Python SDK，它原生支持 `base_url` 参数，**不需要换 SDK**。多 image content blocks（WebP base64）+ 文本 prompt 的请求格式遵循 Anthropic Messages API 规范 —— 任何兼容该规范的代理都可以直接对接。

> 💡 如果你的 shell 已经导出了 `ANTHROPIC_AUTH_TOKEN`（Claude Code 的命名），本项目会自动把它当作 `ANTHROPIC_API_KEY` 用，不需要把 token 复制到 `.env`。明确设了 `ANTHROPIC_API_KEY` 的话它优先。

## 接入 Claude Code

### 方式一：`.mcp.json`（推荐）

在项目根目录创建 `.mcp.json`：

```json
{
  "mcpServers": {
    "screen-mcp": {
      "command": "C:\\Users\\xzs\\Desktop\\mcp_test\\.venv\\Scripts\\screen-mcp.exe"
    }
  }
}
```

然后在 `~/.claude/settings.json` 里批准这个 server：

```json
{
  "enabledMcpjsonServers": ["screen-mcp"]
}
```

### 方式二：`claude mcp add` 命令

```bash
# 在项目目录下
claude mcp add screen-mcp -- .venv/Scripts/screen-mcp.exe
```

之后在 Claude Code 会话里，6 个 tool 就以 `start_capture`、`stop_capture`、`capture_now`、`set_polling`、`list_windows`、`analyze_screen` 的名字可用。

## 端到端示例

一个典型的 agent 交互流程：

> **用户：** 列出我打开的窗口。
>
> **Agent：** *(调用 `list_windows`)* —— 我看到 Notepad (hwnd 0x1a2b3c) 和 Visual Studio Code (hwnd 0x4d5e6f)。
>
> **用户：** 对 Notepad 窗口开 3 秒一次的轮询采集。
>
> **Agent：** *(调用 `start_capture("window", "Notepad")`，然后
> `set_polling(enabled=True, interval_seconds=3)`)*
>
> **用户：** 我刚才打了什么？
>
> **Agent：** *(调用 `analyze_screen("我刚才打了什么?", lookback_frames=3)`)*
> 你写的是："回家路上买点牛奶。"

## MCP tool 参考

### `start_capture(mode: 'fullscreen'|'window', target?: str)`
启动采集会话。返回 `{session_id, mode, target, state}`。
- `mode='window'` 必须传 `target`（窗口标题子串或十六进制 hwnd）
- 错误：会话已 active 时抛 `SessionError`；在 Linux/macOS 上请求 `mode='window'` 抛 `UnsupportedPlatformError`

### `stop_capture()`
结束当前会话。返回 `{stopped, state}`。

### `capture_now()`
按需截一次图。返回帧的**元信息**（不含图片字节，避免 MCP 消息体爆炸）：
`{frame_id, captured_at, width, height, phash, format, metadata}`。

### `set_polling(enabled: bool, interval_seconds: float = 3.0)`
开关后台轮询循环。返回 `{polling, interval}`。

### `list_windows()`
枚举可见的顶层窗口。Windows 上返回 `[{hwnd, id, title, pid, bbox}]`，Linux/macOS 上返回 `[]`。

### `analyze_screen(query: str, lookback_frames: int = 3)`
把最近若干帧发给视觉模型。返回
`{text, frame_ids, region_count, regions, model, tokens_used?}`。

## 开发

```bash
# 跑所有测试
pytest tests/ -v

# 跑某个模块的测试
pytest tests/test_session.py -v

# 启动 server（stdio 模式，会等 stdin）
python -m screen_mcp.server
```

### Linux 显示器说明

在无显示器的 Linux 主机上，`tests/test_capture.py` 和 `tests/test_capture_linux.py` 会**mock 掉 mss 库**，让测试套件在没 X server 的情况下也能跑。要跑**真实的 mss 采集路径**：

```bash
# Ubuntu / Debian
sudo apt install xvfb
xvfb-run -a pytest tests/test_capture_linux.py -v -k real_mss
```

`test_real_mss_capture_under_xvfb` 测试在没装 `Xvfb` 时会自动 skip。

### 已知问题与修复

**`mss.shot(output=BytesIO)` 的陷阱** — 早期版本里 `MssBackend.capture_frame` 把 `io.BytesIO()` 当成 `output` 参数传给 `sct.shot()`，导致 `'_io.BytesIO' object has no attribute 'format'` 错误。

原因：`mss.shot()` 的 `output` 参数期望的是**文件名模板字符串**（如 `"{mon}.png"`），不是 file-like 对象；mss 内部会对它调用 `.format()`，而 BytesIO 没有这个方法。

修复方案：先 `sct.shot(mon=1)` 拿到返回的文件名，读出 bytes 再删掉临时文件：

```python
filename = sct.shot(mon=1)
try:
    with open(filename, "rb") as f:
        return f.read()
finally:
    os.remove(filename)
```

### 项目结构

```
src/screen_mcp/
├── server.py             # FastMCP 入口
├── tools.py              # 6 个 tool 的实现
├── session.py            # 单例 session + 状态机
├── frame.py              # Frame + pHash 去重 buffer
├── config.py             # pydantic-settings 配置
├── capture/
│   ├── base.py           # CaptureBackend Protocol + Target
│   ├── mss_backend.py    # 跨平台全屏
│   └── windows_backend.py# Windows PrintWindow 单窗口
└── vision/
    ├── base.py           # VisionProvider Protocol
    └── anthropic.py      # AnthropicVisionProvider（默认实现，支持 base_url 代理）
```

## License

TBD.
