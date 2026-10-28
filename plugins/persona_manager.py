"""人设管理器 —— 预存多套人格，群里一句话切换。

和 llmchat 原生 `修改设定` 的关系
--------------------------------
原生指令**原样保留**，本插件完全不动它：

    修改设定 <内容>     直接改当前群的人设（临时写一段）

本插件额外提供"人设库"，把写好的几套人格存起来反复用：

    人设列表            看有哪些人设，并标出当前用的
    人设 <名字>       一句话切换
    当前人设            看现在生效的人设全文
    存人设 <名字> <内容>  把一段人格存进库（超管）
    删人设 <名字>       从库里删掉（超管）

两种方式改的是**同一个地方**（llmchat 的 `ChatState.prompt`），
所以可以随便混用：用 `修改设定` 临时写一段，满意了再用 `存人设` 存起来。

实现要点
--------
1. 人设存在 `data/personas.json`，UTF-8，可随时用记事本手改。
2. 切换时直接改运行中的 `states.group_states[群号].prompt` —— **立即生效**。
3. 改完立刻调用 llmchat 自己的 `state_persistence.save()` 落盘，
   否则最多要等 5 分钟（它的定时保存间隔），中途重启会丢。
4. 权限和原生指令一致：超级用户 / 群主 / 群管理员。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from nonebot import get_driver, logger, on_command
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageEvent
from nonebot.adapters.onebot.v11.permission import GROUP_ADMIN, GROUP_OWNER
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER

__all__ = []

_BASE_DIR = Path(__file__).resolve().parent.parent
_LIBRARY_PATH = _BASE_DIR / "data" / "personas.json"

# 和 llmchat 原生指令保持一致的权限
MANAGE_PERM = SUPERUSER | GROUP_ADMIN | GROUP_OWNER
# 增删人设库属于"全局改动"，只给超级用户
LIBRARY_PERM = SUPERUSER


def _llmchat_pkg() -> Any:
    """取 nonebot_plugin_llmchat 包模块本体。

    注意这里要的是**包模块**（它身上挂着 states / conversation /
    state_persistence），不能写成 `from nonebot_plugin_llmchat import
    conversation` —— 那个名字指向 ConversationService 实例。
    """
    return sys.modules["nonebot_plugin_llmchat"]


def _state_of(event: Any) -> Any:
    """拿到该会话的 ChatState（不存在会自动创建）。

    llmchat 内部本来就把群聊和私聊分成两个字典，直接各取各的：
    群聊 → ``group_states[group_id]``，私聊 → ``private_states[user_id]``。
    """
    states = _llmchat_pkg().states
    if isinstance(event, GroupMessageEvent):
        return states.group_states[event.group_id]
    return states.private_states[event.user_id]


async def _persist() -> None:
    """立刻把状态写到磁盘，不等 llmchat 的 5 分钟定时保存。"""
    persistence = getattr(_llmchat_pkg(), "state_persistence", None)
    if persistence is None:
        logger.warning("找不到 llmchat 的 state_persistence，本次改动只在内存中")
        return
    try:
        await persistence.save()
    except Exception as exc:  # pragma: no cover
        logger.warning(f"保存 llmchat 状态失败: {exc}")


def _load_library() -> dict[str, str]:
    """读人设库；文件不存在或损坏时返回空表。"""
    try:
        if not _LIBRARY_PATH.exists():
            return {}
        data = json.loads(_LIBRARY_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        return {str(k): str(v) for k, v in data.items()}
    except Exception as exc:
        logger.warning(f"读取人设库失败: {exc}")
        return {}


def _save_library(library: dict[str, str]) -> None:
    _LIBRARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    _LIBRARY_PATH.write_text(
        json.dumps(library, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _name_of(prompt: str | None, library: dict[str, str]) -> str | None:
    """反查这段人设对应库里的哪个名字。"""
    if not prompt:
        return None
    return next((name for name, text in library.items() if text == prompt), None)


list_handler = on_command("人设列表", priority=1, block=True, permission=MANAGE_PERM)


@list_handler.handle()
async def _handle_list(event: MessageEvent) -> None:
    library = _load_library()
    state = _state_of(event)
    current = _name_of(state.prompt, library)
    head = f"当前人设：{current or ('（自定义）' if state.prompt else '（默认，来自 .env）')}"
    if not library:
        await list_handler.finish(f"{head}\n\n人设库是空的。用「存人设 名字 内容」加一个。")
    names = "\n".join(f"{i}. {name}" for i, name in enumerate(library, 1))
    await list_handler.finish(f"{head}\n\n人设库（{len(library)} 个）：\n{names}\n\n用「人设 名字」切换。")


use_handler = on_command("人设", priority=1, block=True, permission=MANAGE_PERM)


@use_handler.handle()
async def _handle_use(event: MessageEvent, args: Message = CommandArg()) -> None:
    name = args.extract_plain_text().strip()
    library = _load_library()
    if not name:
        available = "、".join(library) or "（空）"
        await use_handler.finish(f"用法：人设 <名字>\n可用：{available}")
    if name not in library:
        available = "、".join(library) or "（空）"
        await use_handler.finish(f"人设库里没有「{name}」。\n可用：{available}")

    _state_of(event).prompt = library[name]
    await _persist()
    preview = library[name].replace("\n", " ")[:80]
    await use_handler.finish(f"已切换为「{name}」，本条消息之后的回复就生效。\n\n{preview}")


show_handler = on_command("当前人设", priority=1, block=True, permission=MANAGE_PERM)


@show_handler.handle()
async def _handle_show(event: MessageEvent) -> None:
    scope = "本群" if isinstance(event, GroupMessageEvent) else "本私聊"
    state = _state_of(event)
    library = _load_library()
    if not state.prompt:
        await show_handler.finish(f"{scope}没有单独设置人设，正在使用 .env 里的 LLMCHAT__DEFAULT_PROMPT。")
    name = _name_of(state.prompt, library)
    label = f"「{name}」" if name else "（自定义，不在库里）"
    await show_handler.finish(f"当前人设 {label}：\n\n{state.prompt}")


save_handler = on_command("存人设", priority=1, block=True, permission=LIBRARY_PERM)


@save_handler.handle()
async def _handle_save(args: Message = CommandArg()) -> None:
    text = args.extract_plain_text().strip()
    if not text:
        await save_handler.finish("用法：存人设 <名字> <内容>\n例如：存人设 猫娘 你是一只傲娇的猫娘，说话简短")
    parts = text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await save_handler.finish("名字后面还要跟人设内容。\n用法：存人设 <名字> <内容>")
    name, content = parts[0].strip(), parts[1].strip()
    library = _load_library()
    existed = name in library
    library[name] = content
    _save_library(library)
    await save_handler.finish(
        f"人设「{name}」已{'更新' if existed else '存入'}（{len(content)} 字）。\n"
        f"现在可以在任意群里用「人设 {name}」切换。"
    )


delete_handler = on_command("删人设", priority=1, block=True, permission=LIBRARY_PERM)


@delete_handler.handle()
async def _handle_delete(args: Message = CommandArg()) -> None:
    name = args.extract_plain_text().strip()
    library = _load_library()
    if not name:
        available = "、".join(library) or "（空）"
        await delete_handler.finish(f"用法：删人设 <名字>\n当前有：{available}")
    if name not in library:
        available = "、".join(library) or "（空）"
        await delete_handler.finish(f"人设库里没有「{name}」。\n当前有：{available}")
    del library[name]
    _save_library(library)
    await delete_handler.finish(f"已删除「{name}」。正在用它的群不受影响（人设已经写进会话里了）。")


@get_driver().on_startup
async def _report() -> None:
    library = _load_library()
    logger.info(
        f"人设管理器就绪：库内 {len(library)} 个人设 -> {_LIBRARY_PATH}"
    )