# TinyAgent

一个面向 OpenAI-compatible 接口的简易 Python 对话式 Agent，适合作为最小可运行原型继续演进。

## 特性

- 仅依赖 `config.yaml` 读取模型配置
- 支持 OpenAI-compatible `base_url`
- 支持基础命令：`/quit`、`/model`
- 支持 `streaming`、`reasoning`、`answer` 展示开关
- 支持上下文窗口限制与自动压缩
- 支持全屏 CLI/TUI 交互
- 支持 debug 模式记录请求/响应日志

## 目录结构

```text
TinyAgent/
├─ main.py
├─ config.yaml.example
├─ requirements.txt
├─ tinyagent/
│  ├─ app.py
│  ├─ config.py
│  ├─ conversation.py
│  ├─ debug.py
│  ├─ session.py
│  ├─ status.py
│  ├─ terminal.py
│  └─ utils.py
└─ temp/
```

## 模块职责

- `main.py`
  - 程序入口，仅负责启动应用
- `tinyagent/app.py`
  - 启动流程、运行参数解析、OpenAI 客户端初始化
- `tinyagent/config.py`
  - `config.yaml` 解析、配置数据结构、`--debug=True` 运行参数解析
- `tinyagent/session.py`
  - Agent 主编排流程，连接 UI、上下文、LLM 请求和 debug 记录
- `tinyagent/conversation.py`
  - 对话消息历史、上下文 token 估算、上下文压缩与裁剪
- `tinyagent/status.py`
  - CLI 底栏状态管理，如 `Idle / Thinking / Answering / Compressing`
- `tinyagent/terminal.py`
  - 全屏 TUI、输入输出区、滚动、鼠标交互、剪贴板交互
- `tinyagent/debug.py`
  - debug 会话目录、请求/响应日志、stream chunk 记录
- `tinyagent/utils.py`
  - 通用工具函数，如脱敏、JSON 落盘、文本提取、剪贴板读写

## 安装

```bash
pip install -r requirements.txt
```

## 配置

先复制配置模板：

```bash
copy config.yaml.example config.yaml
```

然后修改 `config.yaml`：

```yaml
openai:
  base_url: "https://api.openai.com/v1"
  api_key: "sk-your-key"
  model: "gpt-4.1-mini"
  timeout: 600
  extra_headers: {}

chat:
  system_prompt: "You are a concise and helpful assistant."
  streaming: true
  show_reasoning: false
  show_answer: true
  context_limit_tokens: 128000
  enable_context_compression: true
  compression_threshold: 0.75
  compression_keep_last_turns: 4
  summary_model: ""
```

## 运行

普通模式：

```bash
python main.py
```

开启 debug：

```bash
python main.py --debug=True
```

## 交互说明

- 输入命令：
  - `/quit`：退出
  - `/model`：查看当前模型
  - `/model <name>`：切换模型
- 输出区支持鼠标滚轮滚动
- 输出区支持：
  - 左键拖选文本
  - 右键复制选中内容
- 输入区支持：
  - 右键粘贴剪贴板内容
- 快捷键：
  - `Tab`：切换输入区/输出区焦点
  - `Esc`：返回输入区
  - `PageUp` / `PageDown`：翻页
  - `Home` / `End`：跳到历史首尾

## Debug 日志

开启 `--debug=True` 后，每次启动都会在 `temp/logs` 下生成独立目录：

```text
temp/logs/.<sessionID(md5)>-<datetime>/
```

目录内典型内容：

```text
session.json
requests.jsonl
rounds/
  req-0001/
    request.json
    response.json
    stream.jsonl
```

记录内容包括：

- 请求体参数
- 响应体内容
- streaming chunk
- provider completion id
- finish reason
- usage/token
- 请求耗时
- 上下文压缩信息

## 开发说明

- `config.yaml` 默认忽略提交，避免误传密钥
- `config.yaml.example` 用于共享配置模板
- `temp/` 为运行期日志目录
- `.vendor/` 为本地依赖缓存目录，不建议提交

## 二次开发建议

- 如果要新增命令：
  - 优先修改 `tinyagent/session.py` 中的命令分发逻辑
- 如果要调整上下文压缩策略：
  - 优先修改 `tinyagent/conversation.py`
- 如果要扩展 debug 字段：
  - 优先修改 `tinyagent/debug.py`
- 如果要调整界面交互：
  - 优先修改 `tinyagent/terminal.py`
- 如果要支持更多运行参数：
  - 优先修改 `tinyagent/config.py`
- 如果要替换不同 LLM Provider：
  - 先确认其 OpenAI-compatible 字段差异，再补充到 `tinyagent/debug.py` 和 `tinyagent/utils.py`
- 如果后续功能继续增长：
  - 建议下一步把 `tinyagent/session.py` 中的请求执行逻辑继续下沉到独立 `llm_service.py`

## 后续可扩展方向

- 增加 `/clear`、`/compress`、`/system`
- 拆分 LLM 请求服务层
- 补充单元测试
- 支持更多 provider 兼容字段
