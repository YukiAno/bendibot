# bendibot

一个跑在 QQ 上的聊天机器人：**NapCat**（协议端）+ **NoneBot2**（框架）+ **nonebot-plugin-llmchat**（对话），走 **DeepSeek 官方 API**，另带一套 **Project SEKAI** 数据查询。

```
QQ 消息
  ↓
NapCat（第三方 QQ 协议端，自带独立 QQ 客户端）
  ↓  反向 WebSocket  ws://127.0.0.1:8080/onebot/v11/ws
NoneBot2（本仓库）
  ├── nonebot-plugin-llmchat → DeepSeek API（对话）
  └── plugins/pjsk           → 公开展示数据（歌曲/卡牌/活动/榜线）
  ↓
所有出站消息统一经过 send_guard 闸门
  ↓
NapCat → QQ
```

---

## 功能

| 模块 | 说明 |
|---|---|
| **AI 对话** | DeepSeek API，支持图片识别（图 + @ 同一条消息） |
| **PJSK 查询** | 16 条指令：活动、歌曲、谱面、曲绘、卡牌、卡面、卡池、角色、榜线、预测、组卡、实效 |
| **拟人化延迟** | 按消息长度分档延迟，避免秒回（可关） |
| **回复风格** | 5 种：紧凑 / 自然 / 分条 / 碎版 / 详细，控制一次回复拆几条 |
| **人设库** | 6 套预设 + 支持自定义保存 |
| **出站闸门** | 每群/全局限流 + 被拦次数熔断静默，防止群友刷指令触发风控 |
| **群白名单** | 只在指定群响应；非白名单群的消息在进入任何插件前就被丢弃 |

> 固定指令（PJSK 那 16 条）走 matcher 直接处理，**不消耗 AI token**；只有真正的聊天才会调 API。

---

## 目录结构

```
bendibot/
├── bot.py                     入口（nonebot.init + 加载插件）
├── pyproject.toml             适配器与插件声明
├── .env.example               配置模板（复制成 .env 后填值）
├── .gitignore                 ★ 已排除 .env / data / napcat / venv
│
├── plugins/                   自写插件
│   ├── llmchat_delay.py       拟人化延迟
│   ├── llmchat_style.py       回复风格（拆条）
│   ├── persona_manager.py     人设库
│   ├── send_guard.py          ★ 出站闸门（限流 + 熔断）
│   ├── group_whitelist.py     ★ 群白名单
│   └── pjsk/                  PJSK 集成
│       ├── providers/         数据源（masterdata / sekai.best / 33Kit）
│       ├── services/          业务逻辑（活动、卡牌、组卡…）
│       └── commands/          指令层
│
├── pjsk_mcp.py                把 PJSK 能力暴露成 MCP 工具给 AI 用
├── pjsk_mcp_check.py          MCP 诊断
├── bot_manager.py             进程管理（只结束占 8080 的 python）
├── check_status.py            状态检查（端口/进程/配置）
├── toggle_silent.py           静音开关
└── *.bat                      运维脚本（启动/停止/检查/静音）
```

运行时会生成：

```
data/            对话记忆、人设、风格、PJSK 缓存、闸门日志（已 gitignore）
napcat/          NapCat 与自带 QQ（约 1.5 GB，已 gitignore）
venv/            Python 虚拟环境（已 gitignore）
```

---

## 快速开始

### 1. 环境

- Windows / Python **3.12**
- 一个可登录的 QQ 号（**建议用小号**，见底部风险说明）

```powershell
python -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
# 或直接：.\venv\Scripts\python.exe -m pip install nonebot2[fastapi] nonebot-adapter-onebot nonebot-plugin-llmchat
```

### 2. 配置

```powershell
copy .env.example .env
```

然后编辑 `.env`，至少要填：

| 键 | 说明 |
|---|---|
| `LLMCHAT__API_PRESETS` | DeepSeek API 配置（含 `api_key`） |
| `ONEBOT_ACCESS_TOKEN` | 与 NapCat 反向 WS 的 token，两边必须一致 |
| `SUPERUSERS` | 超级用户 QQ 号（JSON 数组） |
| `GROUP_WHITELIST` | 允许响应的群号，逗号分隔；**为空 = 所有群都不回复** |

DeepSeek 密钥在 <https://platform.deepseek.com/api_keys> 获取。

### 3. 启动框架

```powershell
.\启动bot.bat
```

窗口出现 `Uvicorn running on http://127.0.0.1:8080` 即成功。**这个窗口要一直开着。**

### 4. 接入 NapCat

1. 安装 NapCat（Shell 或 OneKey 模式皆可），扫码登录 bot 号
2. 在 NapCat 的 OneBot 配置里新增 **WebSocket 客户端**，URL 填：
   ```
   ws://127.0.0.1:8080/onebot/v11/ws
   ```
3. token 填成 `.env` 里 `ONEBOT_ACCESS_TOKEN` 的值（**两边不一致会 403**）

### 5. 验证

```powershell
.\检查状态.bat
```

看到 `8080 ESTABLISHED` 说明 NapCat 已连上（只有 `LISTENING` 只是框架在跑）。

---

## 指令一览

### PJSK（16 条）

指令**直接发**，不用 @，不用加 `/`。区服可省略（默认国服），也可写 `日服` / `台服` / `韩服` / `国际服`。

| 类别 | 指令 |
|---|---|
| 活动 | `查活动` |
| 实效 | `实效 <队长> [队友1~4]` |
| 歌曲 | `查歌曲 <关键词>`、`查谱面 <关键词>`、`查曲绘 <关键词>`、`加别名 <ID> <别名>` |
| 卡牌 | `查卡 <角色/卡名>`、`卡面 <卡ID/角色> [花前\|花后]`、`查卡池`、`查角色` |
| 榜线 | `查榜线 [档位[+/-]]`、`查预测`（仅日服） |
| 组卡 | `组卡 <歌名> [难度] [活动ID\|无活动] [按综合力]` |
| 杂项 | `pjsk帮助`、`pjsk数据源`、`pjsk清缓存` |

> 数据全部来自公开源，不需要游戏账号。指令实现见 `plugins/pjsk/commands/`。

### llmchat 原生

`修改设定 <内容>`、`记忆清除`、`API预设 <名字>`、`设置主动回复概率 <0~1>`、`切换思维输出`

### 自写

`人设 <名字>` / `人设列表` / `当前人设` / `存人设` / `删人设`
`风格 <名字>` / `风格列表` / `当前风格`

---

## 配置要点

### 群白名单（`GROUP_WHITELIST`）

```
GROUP_WHITELIST_ENABLE=true
GROUP_WHITELIST=123456789,987654321
```

- **为空 = 所有群都不回复**（保守默认）
- 拦在事件入口：非白名单群的消息**不会进入任何插件**，连 AI 都不调用
- 私聊不受限制
- 改完 **5 秒生效**，不用重启

### 出站闸门（`SEND_GUARD_*`）

| 键 | 默认 | 含义 |
|---|---|---|
| `SEND_GUARD_ENABLE` | true | 总开关 |
| `SEND_GUARD_SILENT` | false | 全静默（在线但一条不发） |
| `SEND_GUARD_GROUP_PER_MINUTE` | 10 | 每群每分钟上限 |
| `SEND_GUARD_GROUP_PER_HOUR` | 120 | 每群每小时上限 |
| `SEND_GUARD_GLOBAL_PER_MINUTE` | 20 | 全局每分钟上限 |
| `SEND_GUARD_MUTE_THRESHOLD` | 10 | 一分钟被拦这么多次就熔断 |
| `SEND_GUARD_MUTE_SECONDS` | 600 | 熔断静默时长 |

被拦的消息**静默丢弃**。

---

## ⚠️ 风险与合规

1. **NapCat 是第三方 QQ 协议实现，违反腾讯用户协议**，账号可能被限制或封禁。请自行判断。
2. **不要用日常主号当 bot**，也不要把主号登录在运行 NapCat 的同一台机器上。
3. **AI 输出不可控**，接入群聊前建议先开白名单小范围试。
4. **DeepSeek 按 token 计费**，群越活跃、`HISTORY_SIZE` 越大，花费越高。
5. 若不想承担封号风险，可改用 **QQ 官方机器人平台**（无封号风险，但群聊权限与功能受限明显）。

---

## 许可证与依赖说明

- **本项目以 [GPL-3.0](LICENSE) 发布。**
  原因：依赖 **GPL-3.0** 的 [`nonebot-plugin-llmchat`](https://github.com/FuQuan233/nonebot-plugin-llmchat)，
  且 `plugins/llmchat_delay.py`、`plugins/llmchat_style.py` 通过**运行时补丁**修改了它的内部行为，
  属于其衍生作品，因此本项目采用与之兼容的 GPL-3.0。

- **NapCat / QQ 客户端不包含在本仓库中。**
  NapCat 需自行从 <https://github.com/NapNeko/NapCatQQ> 安装。
  它采用自有的 **Limited Redistribution License**（禁止未授权使用与再分发、修改后的代码不得公开发布、
  禁止商业用途），请自行阅读并遵守。本仓库仅通过公开的 **OneBot v11 协议**与协议端通信，
  不包含、不修改、也不再分发 NapCat 的任何代码。

- **NoneBot2 与 nonebot-adapter-onebot 均为 [MIT](https://opensource.org/licenses/MIT) 许可**，可自由使用、修改、分发。

- 本项目仅用于个人学习与朋友间小范围使用，**不用于任何商业目的**。
