"""拟人化回复延迟 —— 让 llmchat 的回复不再"秒回"。

背景
----
llmchat 0.6.0 没有任何延迟配置项。它唯一的时间控制是
`message_utils.send_reply_messages` 里硬编码的 `asyncio.sleep(2)`，
而且那只作用于"多条消息之间"，不影响第一条的发送时机。

原理
----
`conversation.py` 通过模块全局名调用 `send_reply_messages`
（见该文件第 151、221 行），因此替换掉
`nonebot_plugin_llmchat.conversation.send_reply_messages` 即可接管发送时机。

⚠️ 踩过的坑（务必保留这段注释）
--------------------------------
**不能**这样取模块::

    from nonebot_plugin_llmchat import conversation      # ❌ 拿到的是实例！

因为 llmchat 的 `__init__.py` 里有::

    conversation = ConversationService(...)

这个同名变量**遮蔽**了 `conversation.py` 子模块。于是上面那行拿到的是
ConversationService 实例；往它身上赋 `send_reply_messages` 属性不会报错，
但也完全没有作用 —— 表面上"patch 成功"，实际延迟从未生效。

同理 `import nonebot_plugin_llmchat.conversation as c` 也会拿到实例。

**正确做法**是按模块全名从 `sys.modules` 取（必要时用
`importlib.import_module`），本文件的 `_llmchat_module()` 就是干这个的。

延迟规则（按字数分档）
----------------------
**短消息**（字数 ≤ THRESHOLD，默认 12 字）::

    延迟 = random(SHORT_MIN, SHORT_MAX)

**长消息**（字数 > THRESHOLD）—— 越长等越久::

    延迟 = random(LONG_MIN, LONG_MAX) + (字数 - THRESHOLD) × LONG_PER_CHAR

    最终不超过 CEILING（默认 10 秒）

默认效果：

    3 字   → 2.0 ~ 4.0 秒
    12 字  → 2.0 ~ 4.0 秒
    13 字  → 3.0 ~ 5.1 秒
    20 字  → 3.8 ~ 5.8 秒
    30 字  → 4.8 ~ 6.8 秒
    50 字  → 6.8 ~ 8.8 秒
    80 字+ → 10 秒（封顶）

**参数热读取**：每次发送前重新读取 `.env`，改参数**不需要重启机器人**。

**诊断日志**：`data\\delay_log.txt`，记录加载、补丁、每次延迟与发送。

.env 参数一览：

    REPLY_DELAY_ENABLE       总开关（false 则完全恢复 llmchat 原行为）
    REPLY_DELAY_THRESHOLD    长短消息的分界字数（默认 12）
    REPLY_DELAY_SHORT_MIN    短消息延迟下限（秒）
    REPLY_DELAY_SHORT_MAX    短消息延迟上限（秒）
    REPLY_DELAY_LONG_MIN     长消息延迟下限（秒）
    REPLY_DELAY_LONG_MAX     长消息延迟上限（秒）
    REPLY_DELAY_LONG_PER_CHAR 长消息超出分界后，每多一字的加成（秒）
    REPLY_DELAY_CEILING      单条延迟总上限（秒）
"""

from __future__ import annotations

import asyncio
import importlib
import random
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable

from dotenv import dotenv_values
from nonebot import get_driver, logger

# 用 `from 包.子模块 import 名字` 的形式是安全的：
# 它会真正导入子模块，不受 __init__.py 里同名变量的遮蔽影响。
from nonebot_plugin_llmchat.message_utils import Message as _Message
from nonebot_plugin_llmchat.message_utils import parse_reply_segments

__all__ = ["send_reply_messages"]

_BASE_DIR = Path(__file__).resolve().parent.parent
_ENV_PATH = _BASE_DIR / ".env"
_LOG_PATH = _BASE_DIR / "data" / "delay_log.txt"

#: 诊断日志超过这个大小就截掉前一半（防止无限增长）
#: 与 send_guard.py 用同一套策略，保持一致
_MAX_LOG_BYTES = 512 * 1024

_CONVERSATION = "nonebot_plugin_llmchat.conversation"
_MESSAGE_UTILS = "nonebot_plugin_llmchat.message_utils"

# 默认值（.env 没写或写错时使用）
_DEFAULTS: dict[str, float] = {
    "short_min": 2.0,
    "short_max": 4.0,
    "long_min": 3.0,
    "long_max": 5.0,
    "threshold": 12.0,
    "per_char": 0.1,
    "ceiling": 10.0,
}


def _llmchat_module(full_name: str) -> Any:
    """按模块全名取真正的模块对象。

    绝不能写成 `from nonebot_plugin_llmchat import conversation`
    —— 那会拿到被 __init__.py 遮蔽的 ConversationService 实例。
    """
    module = sys.modules.get(full_name)
    if module is None:
        module = importlib.import_module(full_name)
    return module


def _log(text: str) -> None:
    """写诊断日志；失败也不影响正常流程。

    超过 ``_MAX_LOG_BYTES`` 就**保留后半段** ——
    排查风控问题时最近的数据才有用，久远的没价值。
    """
    try:
        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        if _LOG_PATH.exists() and _LOG_PATH.stat().st_size > _MAX_LOG_BYTES:
            lines = _LOG_PATH.read_text(encoding="utf-8").splitlines()
            kept = lines[len(lines) // 2:]
            _LOG_PATH.write_text("\n".join(kept) + "\n", encoding="utf-8")
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with _LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(f"[{stamp}] {text}\n")
    except Exception:
        pass


def _segment_limit() -> int:
    """当前群允许的最大条数（0 = 不限制）。

    由风格插件 plugins/llmchat_style.py 按群决定；它没加载或出错时返回 0，
    也就是"不做限制"，保证本延迟插件单独也能正常工作。
    """
    module = sys.modules.get("plugins.llmchat_style")
    if module is None:
        return 0
    try:
        return int(module.current_limit())
    except Exception:
        return 0


def _read_params() -> dict[str, Any]:
    """重新读取 .env，拿到最新的参数。读不到就用默认值。"""
    env: dict[str, str] = {}
    try:
        env = {k: v for k, v in dotenv_values(_ENV_PATH).items() if v is not None}
    except Exception as exc:  # pragma: no cover
        _log(f"读取 .env 失败，使用默认参数: {exc}")

    def num(key: str, default: float) -> float:
        raw = (env.get(key) or "").strip()
        if not raw:
            return default
        try:
            return float(raw)
        except ValueError:
            _log(f"{key}={raw!r} 不是合法数字，使用默认值 {default}")
            return default

    def flag(key: str, default: bool) -> bool:
        raw = (env.get(key) or "").strip().lower()
        if not raw:
            return default
        return raw not in {"0", "false", "no", "off", "disable", "disabled"}

    return {
        "enabled": flag("REPLY_DELAY_ENABLE", True),
        "threshold": num("REPLY_DELAY_THRESHOLD", _DEFAULTS["threshold"]),
        "short_min": num("REPLY_DELAY_SHORT_MIN", _DEFAULTS["short_min"]),
        "short_max": num("REPLY_DELAY_SHORT_MAX", _DEFAULTS["short_max"]),
        "long_min": num("REPLY_DELAY_LONG_MIN", _DEFAULTS["long_min"]),
        "long_max": num("REPLY_DELAY_LONG_MAX", _DEFAULTS["long_max"]),
        "per_char": num("REPLY_DELAY_LONG_PER_CHAR", _DEFAULTS["per_char"]),
        "ceiling": num("REPLY_DELAY_CEILING", _DEFAULTS["ceiling"]),
    }


def _delay_for(text: str, index: int, p: dict[str, Any]) -> float:
    """按字数决定等待多久：短句用短区间，长句用长区间并随字数递增。"""
    length = len(text)
    if length <= p["threshold"]:
        delay = random.uniform(p["short_min"], p["short_max"])
    else:
        delay = random.uniform(p["long_min"], p["long_max"])
        delay += (length - p["threshold"]) * p["per_char"]
    if p["ceiling"] > 0:
        delay = min(delay, p["ceiling"])
    return max(0.0, delay)


async def send_reply_messages(
    sender: Callable[[Any], Awaitable[Any]],
    content: str,
) -> None:
    """替换 llmchat 的同名函数：在每条消息发送前插入动态延迟。"""
    segments = parse_reply_segments(content)
    if not segments:
        _log("收到空回复，不发送")
        return

    # 条数上限：由风格插件（plugins/llmchat_style.py）按群决定。
    # 风格里写了"最多 N 条"，但模型不一定听话，所以这里硬性兜底：
    # 超出就把多余的内容合并进最后一条，用换行连接。
    limit = _segment_limit()
    if limit > 0 and len(segments) > limit:
        produced = len(segments)
        segments = segments[: limit - 1] + ["\n".join(segments[limit - 1 :])]
        logger.info(f"回复条数 {produced} 超过上限 {limit}，已合并为 {len(segments)} 条")
        _log(f"条数 {produced} 超上限 {limit}，已合并为 {len(segments)} 条")

    params = _read_params()
    total = len(segments)
    _log(
        f"接管发送：{total} 条，参数 短({'<='}{int(params['threshold'])}字)="
        f"{params['short_min']}~{params['short_max']}s 长={params['long_min']}~"
        f"{params['long_max']}s+{params['per_char']}/字 封顶={params['ceiling']}s "
        f"启用={params['enabled']}"
    )

    for index, segment in enumerate(segments):
        if params["enabled"]:
            delay = _delay_for(segment, index, params)
            if delay > 0:
                logger.info(
                    f"等待 {delay:.2f}s 后发送第 {index + 1}/{total} 条"
                    f"（{len(segment)} 字）"
                )
                _log(f"等待 {delay:.2f}s 后发送第 {index + 1}/{total} 条（{len(segment)} 字）")
                await asyncio.sleep(delay)

        # 构造消息本体。万一失败也不让整条回复丢失。
        try:
            payload: Any = _Message(segment)
        except Exception as exc:
            logger.warning(f"构造消息失败，改发纯文本: {exc}")
            _log(f"构造消息失败，改发纯文本: {exc}")
            payload = segment
        await sender(payload)
        _log(f"第 {index + 1}/{total} 条已发送")


def _patch(reason: str) -> bool:
    """把 llmchat 真正的模块里的函数引用换掉，并记录实际结果。"""
    conv = _llmchat_module(_CONVERSATION)
    mu = _llmchat_module(_MESSAGE_UTILS)

    before = getattr(conv.send_reply_messages, "__module__", "?")
    conv.send_reply_messages = send_reply_messages
    mu.send_reply_messages = send_reply_messages

    after_conv = getattr(conv.send_reply_messages, "__module__", "?")
    after_mu = getattr(mu.send_reply_messages, "__module__", "?")

    ok = after_conv == __name__ and after_mu == __name__
    line = (
        f"[{reason}] 补丁{'成功' if ok else '失败'}："
        f"conversation({before} -> {after_conv}) / message_utils -> {after_mu}"
    )
    logger.info(line)
    _log(line)
    return ok


_patch("模块加载")


@get_driver().on_startup
async def _repatch_on_startup() -> None:
    """全部插件加载完成后再补一次，确保引用没有被覆盖。"""
    _log("-" * 18 + " 启动完成，复查补丁 " + "-" * 18)
    _patch("启动复查")
    p = _read_params()
    _log(
        f"当前参数：启用={p['enabled']} 分界={int(p['threshold'])}字 "
        f"短={p['short_min']}~{p['short_max']}s 长={p['long_min']}~{p['long_max']}s "
        f"每字+{p['per_char']}s 封顶={p['ceiling']}s"
    )