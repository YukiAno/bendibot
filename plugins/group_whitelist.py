"""群白名单 —— 只响应白名单里的群，其余群一律不回复。

为什么放在这一层
----------------
拦在**事件入口**，也就是任何插件（llmchat / pjsk / delay / style）拿到事件之前：

* 非白名单群的消息**根本不会进入 matcher** → 不会调 AI（省 token）
* 也不会被群友「钓」出话来 —— 哪怕有人 @ 它、发「查活动」，都当没看见
* 比在出口拦截更彻底：出口拦只能做到「不发出」，AI 照样白跑一趟

``send_guard`` 里还有一道**同名配置的兜底**，防止某个插件绕过 matcher 直接调 API。

配置（都在 ``.env``，5 秒热读取，改完不用重启）
---------------------------------------------
=================================  ==================  ==================================
键                                  默认                说明
=================================  ==================  ==================================
``GROUP_WHITELIST_ENABLE``          true                总开关
``GROUP_WHITELIST``                 （空）              逗号分隔群号，如 ``123456,789012``
=================================  ==================  ==================================

.. important::
   **``GROUP_WHITELIST`` 为空 = 所有群都不回复**（保守默认）。
   这是故意的：「一个群都不认识」时宁可装死，也不要误回一句。
   要不要放行，看启动日志里那行 ``[whitelist] 生效群白名单``。

私聊不受本插件限制 —— 私聊只给自己用，方便测试。
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import dotenv_values
from nonebot import get_driver, logger
from nonebot.adapters import Event
from nonebot.adapters.onebot.v11 import GroupMessageEvent
from nonebot.exception import IgnoredException
from nonebot.message import event_preprocessor
from nonebot.typing import T_State

_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"

#: 配置缓存（热读取，但不每次都读盘）
_CONF_TTL = 5.0
_conf_cache: tuple[float, dict[str, Any]] = (0.0, {})

#: 被忽略的消息计数，用于「每 N 条打一次日志」，避免刷屏
_ignored = 0
_IGNORED_LOG_EVERY = 20


def _truthy(raw: str | None, default: bool = True) -> bool:
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off", "否", ""}


def _parse_groups(raw: str | None) -> set[str]:
    """把 ``123, 456，789`` 解析成 ``{"123","456","789"}``（兼容中文逗号/空格）。"""
    if not raw:
        return set()
    text = raw.replace("，", ",").replace("、", ",").replace(" ", ",")
    return {p.strip() for p in text.split(",") if p.strip().isdigit()}


def conf() -> dict[str, Any]:
    """读白名单配置（5 秒缓存）。**只读这两个键，不碰任何密钥。**"""
    global _conf_cache
    now = time.time()
    if now - _conf_cache[0] < _CONF_TTL and _conf_cache[1]:
        return _conf_cache[1]

    data: dict[str, Any] = {"enabled": True, "groups": set()}
    try:
        raw = dotenv_values(_ENV_PATH)
        data["enabled"] = _truthy(raw.get("GROUP_WHITELIST_ENABLE"), True)
        data["groups"] = _parse_groups(raw.get("GROUP_WHITELIST"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[whitelist] 读 .env 失败，按「全部不回复」处理：{}", exc)
        data["groups"] = set()

    _conf_cache = (now, data)
    return data


def is_allowed_group(group_id: int | str) -> bool:
    """给 send_guard 兜底用的判断接口。"""
    c = conf()
    if not c["enabled"]:
        return True
    return str(group_id) in c["groups"]


def _log_ignored(group_id: int | str) -> None:
    """被忽略的群消息记一笔（节流到每 20 条一次）。"""
    global _ignored
    _ignored += 1
    if _ignored % _IGNORED_LOG_EVERY != 0:
        return
    try:
        path = Path(__file__).resolve().parent.parent / "data" / "whitelist_log.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"[{stamp}] 累计忽略 {_ignored} 条；最近一条来自群 {group_id}\n")
    except Exception:  # noqa: BLE001
        pass


@event_preprocessor
async def _group_whitelist(event: Event, state: T_State) -> None:
    """非白名单群的群消息：直接丢弃，任何插件都看不到。"""
    if not isinstance(event, GroupMessageEvent):
        return

    c = conf()
    if not c["enabled"]:
        return

    group_id = str(event.group_id)
    if group_id in c["groups"]:
        return

    _log_ignored(group_id)
    # IgnoredException：跳过本事件的**所有** matcher / 预处理器后续逻辑
    raise IgnoredException(f"群 {group_id} 不在白名单")


@get_driver().on_startup
async def _report() -> None:
    """启动时把白名单状态喊出来，避免「以为配好了其实没配」。"""
    c = conf()
    if not c["enabled"]:
        logger.warning("[whitelist] 群白名单**已关闭**（GROUP_WHITELIST_ENABLE=false），"
                       "所有群的消息都会被回复")
        return
    if not c["groups"]:
        logger.warning("[whitelist] ★ 白名单为空：所有群的消息都**不会**被回复 ★ "
                       "（在 .env 的 GROUP_WHITELIST 里填群号，逗号分隔，改完 5 秒内生效）")
        return
    logger.info("[whitelist] 生效群白名单（{} 个）：{}；其余群一律不回复",
                len(c["groups"]), "、".join(sorted(c["groups"])))
