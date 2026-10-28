"""回复风格管理器 —— 一句话切换"拆不拆条"，并硬性限制条数。

问题
----
llmchat 的 `prompts.py` 第 26-27 行硬编码了这样的输出协议::

    日常闲聊通常一句话占一行，每行只表达一个主要意思，并尽量使用简短、自然的口语。
    日常闲聊应积极拆行。逗号或句号两侧能够独立表达时，应改成换行，不要把多个短句挤在同一行。

于是模型把一句完整的话拆成好几条发，观感很"碎"。
人设（`ChatState.prompt`）压不住它 —— 协议区在后面声明
「角色设定……若与输出协议冲突，以输出协议为准」。

做法（两层保险）
----------------
**第一层 · 提示词**：包一层 `build_system_prompt`，把上面那两句替换成当前风格
指定的写法。**不改 site-packages**，插件升级不会丢。

**第二层 · 发送兜底**：风格可以带 `max_lines` 字段。本插件在生成提示词时把
"当前群"记进 ContextVar，发送层（`plugins/llmchat_delay.py`）据此读取上限；
真发出去时如果超过条数，就把多余的内容合并进最后一条。
模型偶尔不听话也不会刷屏。

风格按**群**独立：同一个机器人，A 群可以紧凑、B 群可以分条。

指令（超级用户 / 群主 / 群管理员）
----------------------------------
    风格列表            看有哪些风格，标出当前群的
    风格 <名字>       切换当前群的风格，立即生效
    当前风格            看当前群风格 + 实际写入的提示词 + 条数上限

风格定义存在 `data\\reply_styles.json`，**可以用记事本直接改**；
每个群当前的选择存在 `data\\reply_style_state.json`。

`REPLY_STYLE_MERGE=false` 可以整体关掉本插件（恢复 llmchat 原提示词）。
"""

from __future__ import annotations

import importlib
import json
import sys
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import dotenv_values
from nonebot import get_driver, logger, on_command
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageEvent
from nonebot.adapters.onebot.v11.permission import GROUP_ADMIN, GROUP_OWNER
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER

__all__ = ["current_limit", "style_of", "load_styles"]

_BASE_DIR = Path(__file__).resolve().parent.parent
_STYLES_PATH = _BASE_DIR / "data" / "reply_styles.json"
_STATE_PATH = _BASE_DIR / "data" / "reply_style_state.json"
_LOG_PATH = _BASE_DIR / "data" / "delay_log.txt"
_ENV_PATH = _BASE_DIR / ".env"

_PROMPTS = "nonebot_plugin_llmchat.prompts"
_CONVERSATION = "nonebot_plugin_llmchat.conversation"
_PKG = "nonebot_plugin_llmchat"

MANAGE_PERM = SUPERUSER | GROUP_ADMIN | GROUP_OWNER

#: 默认风格。
#:
#: .. important::
#:   **发送量逐级下调，这是风控防护的一部分。**
#:
#:   2026-10-25：从「分条」改成「自然」（5 条 → 2 条）
#:   2026-10-26：从「自然」改成「紧凑」（2 条 → 1 条）
#:
#:   累计效果：单次回复的发送量降到原来的 **1/5**。
#:
#:   背景：2026-10-25 那天 bot 因群友大量刷指令触发 QQ 风控；
#:   之后换号（旧号被踢下线），新号上线前把发送量压到最低。
#:   **想调高就把这一行改回去**（「自然」或「分条」），
#:   但要知道发送量会跟着涨回去。
DEFAULT_STYLE = "紧凑"

# 原版那两句（固定锚点，逐字匹配 llmchat 0.6.0）
_ANCHOR_LINES = (
    "日常闲聊通常一句话占一行，每行只表达一个主要意思，并尽量使用简短、自然的口语。",
    "日常闲聊应积极拆行。逗号或句号两侧能够独立表达时，应改成换行，不要把多个短句挤在同一行。",
)

# 内置风格；data\reply_styles.json 存在时以文件为准
#   max_lines: 发送时最多几条；0 = 不限制
_BUILTIN_STYLES: dict[str, dict[str, Any]] = {
    "紧凑": {
        "desc": "一句话说完，不拆条（默认）",
        "max_lines": 1,
        "lines": [
            "日常闲聊通常只发一条消息：把想说的合并成一句自然的话说出来，不要为了显得活泼而把它拆碎。",
            "只有确实需要分先后说两件事时才换行；不要输出只有几个字的碎片消息。",
        ],
    },
    "自然": {
        "desc": "真人感：一句说完为主，两件事可以分两条",
        "max_lines": 2,
        "lines": [
            "日常闲聊通常一条消息说完就够；内容确实包含两件不同的事时，可以分成两条。",
            "不要输出只有几个字的碎片消息。",
        ],
    },
    "分条": {
        "desc": "可以拆分成几条短消息，但一次最多 5 条",
        "max_lines": 5,
        "lines": [
            "日常闲聊可以拆成几条短消息发送，把不同意思分开说，这样读起来更像真人在打字。",
            "但一次回复最多 5 条：超过就把后面的内容并进上一条，不要刷屏。",
        ],
    },
    "碎版": {
        "desc": "初版行为：积极拆行，一句拆成几条发（llmchat 原版）",
        "max_lines": 0,
        "lines": list(_ANCHOR_LINES),
    },
    "详细": {
        "desc": "愿意展开说，适合需要解释的群",
        "max_lines": 3,
        "lines": [
            "回答可以适当展开，把话说明白，不要为了短而牺牲信息量。",
            "内容确实分点时分成几条短消息，最多 3 条；日常闲聊仍然一句话说完，不要输出只有几个字的碎片。",
        ],
    },
}

_original_build: Any = None

# 当前正在生成的是哪段会话 —— 提示词阶段写入，发送阶段读取。
# 群聊存 str(group_id)，私聊存 "private_<QQ号>"，两者互不冲突。
_current_ctx: ContextVar[str | None] = ContextVar("llmchat_current_ctx", default=None)


def _module(full_name: str) -> Any:
    """按全名取模块对象。

    llmchat 的 `__init__.py` 里有变量 `conversation = ConversationService(...)`，
    它会遮蔽同名子模块，所以绝不能写 `from nonebot_plugin_llmchat import conversation`。
    """
    module = sys.modules.get(full_name)
    if module is None:
        module = importlib.import_module(full_name)
    return module


def _log(text: str) -> None:
    try:
        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with _LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(f"[{stamp}] {text}\n")
    except Exception:
        pass


def _enabled() -> bool:
    try:
        raw = (dotenv_values(_ENV_PATH).get("REPLY_STYLE_MERGE") or "").strip().lower()
    except Exception:
        return True
    if not raw:
        return True
    return raw not in {"0", "false", "no", "off", "disable", "disabled"}


def load_styles() -> dict[str, dict[str, Any]]:
    """读风格表；文件不存在就用内置的。每次调用都重读，方便记事本直接改。"""
    styles: dict[str, dict[str, Any]] = {}
    try:
        if _STYLES_PATH.exists():
            raw = json.loads(_STYLES_PATH.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                for name, item in raw.items():
                    if isinstance(item, dict) and isinstance(item.get("lines"), list):
                        lines = [str(x) for x in item["lines"]]
                        if len(lines) >= 2:
                            try:
                                max_lines = int(item.get("max_lines", 0) or 0)
                            except (TypeError, ValueError):
                                max_lines = 0
                            styles[str(name)] = {
                                "desc": str(item.get("desc", "")),
                                "lines": lines[:2],
                                "max_lines": max(0, max_lines),
                            }
    except Exception as exc:
        logger.warning(f"读取风格表失败，改用内置风格: {exc}")

    if not styles:
        styles = {k: json.loads(json.dumps(v)) for k, v in _BUILTIN_STYLES.items()}
        try:  # 首次运行时把内置风格落盘，方便用户照着改
            _STYLES_PATH.parent.mkdir(parents=True, exist_ok=True)
            _STYLES_PATH.write_text(
                json.dumps(styles, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            pass

    return styles


def _load_group_choices() -> dict[str, str]:
    try:
        if not _STATE_PATH.exists():
            return {}
        raw = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in raw.items()} if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _save_group_choices(choices: dict[str, str]) -> None:
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _STATE_PATH.write_text(json.dumps(choices, ensure_ascii=False, indent=2), encoding="utf-8")


def _ctx_key_of_state(state: Any) -> str | None:
    """反查这个 ChatState 属于哪个会话（群或私聊）。

    `build_system_prompt` 只拿到 state，没拿到会话标识，所以用身份比较反查。
    群聊返回 ``str(group_id)``，私聊返回 ``private_<user_id>``。
    会话数量很少，遍历开销可以忽略。
    """
    if state is None:
        return None
    try:
        service = sys.modules[_PKG].conversation
        for group_id, group_state in service.states.group_states.items():
            if group_state is state:
                return str(group_id)
        for user_id, private_state in service.states.private_states.items():
            if private_state is state:
                return f"private_{user_id}"
    except Exception:
        pass
    return None


def _ctx_key(event: Any) -> str:
    """从事件取会话键：群聊 → 群号，私聊 → ``private_<QQ号>``。"""
    if isinstance(event, GroupMessageEvent):
        return str(event.group_id)
    return f"private_{event.user_id}"


def style_of(ctx_key: str | None) -> str:
    """该会话当前用的风格名。"""
    if ctx_key is None:
        return DEFAULT_STYLE
    styles = load_styles()
    name = _load_group_choices().get(ctx_key, DEFAULT_STYLE)
    return name if name in styles else DEFAULT_STYLE


def current_limit() -> int:
    """当前回复最多允许几条（0 = 不限制）。

    供发送层（`plugins/llmchat_delay.py`）调用，实现"最多 N 条"的硬性兜底。
    """
    if not _enabled():
        return 0
    ctx_key = _current_ctx.get()
    styles = load_styles()
    item = styles.get(style_of(ctx_key), {})
    return int(item.get("max_lines", 0) or 0)


_VISION_HINT = (
    "用户可以直接发送图片，你能够看到图片内容（照片、截图、图表、表情包等）。"
    "被问到图里有什么时直接描述，不要说你自己无法查看图片。"
    "只有在图片确实没加载出来、看不清或与问题无关时才说明情况。"
)


def _vision_enabled(state: Any) -> bool:
    """该会话当前用的 API 预设是否开启了图片输入。"""
    if state is None:
        return False
    try:
        service = sys.modules[_PKG].conversation
        return bool(service.states.get_preset(state).support_image)
    except Exception:
        return False


def _wrap(original: Any) -> Any:
    def wrapper(*args: Any, **kwargs: Any) -> str:
        text = original(*args, **kwargs)
        if not _enabled():
            return text

        state = kwargs.get("state")
        if state is None and len(args) >= 2:
            state = args[1]

        ctx_key = _ctx_key_of_state(state)
        _current_ctx.set(ctx_key)  # 记下当前会话，发送层要用

        styles = load_styles()
        config = styles.get(style_of(ctx_key))
        if config is None:
            return text

        for old, new in zip(_ANCHOR_LINES, config["lines"]):
            text = text.replace(old, new)

        # 该预设开了 support_image 时，告诉模型它能看图 ——
        # 否则它会因为提示词里没提而回答"我看不了图片"。
        if _vision_enabled(state):
            text = text.replace(
                "\n[角色设定]\n",
                "\n[图片理解]\n" + _VISION_HINT + "\n\n[角色设定]\n",
            )
        return text

    return wrapper


def _patch(reason: str) -> bool:
    """替换 prompts 与 conversation 两个模块里的 build_system_prompt。"""
    global _original_build

    prompts = _module(_PROMPTS)
    conversation = _module(_CONVERSATION)

    if _original_build is None:
        _original_build = prompts.build_system_prompt

    wrapper = _wrap(_original_build)
    prompts.build_system_prompt = wrapper
    conversation.build_system_prompt = wrapper

    ok = (
        getattr(prompts.build_system_prompt, "__name__", "") == "wrapper"
        and getattr(conversation.build_system_prompt, "__name__", "") == "wrapper"
    )

    # 干跑：确认默认风格能命中锚点（用 ConversationService 实例，它才带 config/bot_names）
    hits = 0
    try:
        from nonebot_plugin_llmchat.state import ChatState

        service = sys.modules[_PKG].conversation
        cfg = service.config
        sample_state = ChatState(cfg.default_preset, cfg.history_size, cfg.past_events_size)
        sample = wrapper(
            config=cfg,
            state=sample_state,
            bot_names=service.bot_names,
            is_group=True,
            support_tools=False,
        )
        hits = sum(1 for line in load_styles()[DEFAULT_STYLE]["lines"] if line in sample)
    except Exception as exc:
        logger.debug(f"风格干跑失败（不影响运行）: {exc}")

    line = (
        f"[{reason}] 风格补丁{'成功' if ok else '失败'}：prompts+conversation -> wrapper，"
        f"默认「{DEFAULT_STYLE}」命中 {hits}/2，风格表 {len(load_styles())} 套"
    )
    logger.info(line)
    _log(line)
    return ok


# ---------------------------------------------------------------- 指令

list_handler = on_command("风格列表", priority=1, block=True, permission=MANAGE_PERM)


@list_handler.handle()
async def _handle_list(event: MessageEvent) -> None:
    scope = "本群" if isinstance(event, GroupMessageEvent) else "本私聊"
    styles = load_styles()
    current = style_of(_ctx_key(event))
    rows = []
    for name, item in styles.items():
        limit = int(item.get("max_lines", 0) or 0)
        limit_text = f"最多 {limit} 条" if limit else "不限条数"
        mark = "▶ " if name == current else "  "
        rows.append(f"{mark}{name}（{limit_text}）：{item.get('desc', '')}")
    await list_handler.finish(
        f"当前{scope}风格：{current}\n\n" + "\n".join(rows) + f"\n\n用「风格 名字」切换（只影响{scope}）。"
    )


use_handler = on_command("风格", priority=1, block=True, permission=MANAGE_PERM)


@use_handler.handle()
async def _handle_use(event: MessageEvent, args: Message = CommandArg()) -> None:
    scope = "本群" if isinstance(event, GroupMessageEvent) else "本私聊"
    name = args.extract_plain_text().strip()
    styles = load_styles()
    if not name or name not in styles:
        available = "、".join(styles)
        tip = "没有这个风格。" if name else ""
        await use_handler.finish(f"{tip}用法：风格 <名字>\n可用：{available}")
    choices = _load_group_choices()
    choices[_ctx_key(event)] = name
    _save_group_choices(choices)
    item = styles[name]
    limit = int(item.get("max_lines", 0) or 0)
    limit_text = f"，一次最多 {limit} 条" if limit else "，不限条数"
    await use_handler.finish(
        f"{scope}已切换为「{name}」{limit_text}：{item.get('desc', '')}\n下一条回复就生效。"
    )


show_handler = on_command("当前风格", priority=1, block=True, permission=MANAGE_PERM)


@show_handler.handle()
async def _handle_show(event: MessageEvent) -> None:
    scope = "本群" if isinstance(event, GroupMessageEvent) else "本私聊"
    styles = load_styles()
    current = style_of(_ctx_key(event))
    item = styles.get(current, {})
    limit = int(item.get("max_lines", 0) or 0)
    lines = "\n".join(f"  {x}" for x in item.get("lines", []))
    await show_handler.finish(
        f"{scope}风格：{current}（{item.get('desc', '')}）\n"
        f"条数上限：{limit if limit else '不限'}\n\n"
        f"实际写入提示词的指令：\n{lines}"
    )


@get_driver().on_startup
async def _repatch_on_startup() -> None:
    _log("-" * 18 + " 启动完成，复查风格补丁 " + "-" * 18)
    _patch("启动复查")
    styles = load_styles()
    choices = _load_group_choices()
    logger.info(
        f"回复风格就绪：{len(styles)} 套风格（{'、'.join(styles)}），"
        f"已指定风格的群 {len(choices)} 个"
    )


load_styles()
_patch("模块加载")