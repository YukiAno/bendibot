"""出站消息闸门 —— 风控防护。

为什么需要它
------------
2026-10-25 这个 bot 因为**群友大量刷指令**触发了 QQ 风控，账号进入保护状态。

复盘发现的问题：

* **15 条 PJSK 固定指令零限流** —— 它们走各自 matcher 的 ``finish()``，
  **完全不经过** ``plugins/llmchat_delay``，所以**秒回、无冷却**。
  「查活动 / 查卡 / 实效 / 组卡」这些正好是群友最爱玩的。
* **3% 主动插话**（``LLMCHAT__RANDOM_TRIGGER_PROB``）——
  群里每来一条消息都有概率让 bot 主动发言，而**无触发的自发发言**
  是风控最敏感的行为。
* **「分条」风格**把一次回复拆成最多 5 条 QQ 消息，**发送量放大 5 倍**。

这个插件是**最后一道兜底**：拦在所有出站消息的统一出口上。

拦在哪
------
``nonebot.internal.adapter.bot.Bot.call_api`` —— 这是**唯一**的汇聚点：

* ``matcher.finish()`` / ``matcher.send()`` → ``bot.send()``
* ``bot.send_group_msg()`` / ``bot.send_private_msg()`` （动态方法，也走 call_api）
* llmchat 的 ``send_reply_messages`` → ``bot.send()``

全部都会落到 ``call_api``。所以在这里挂一道闸门，
**任何插件、任何路径发出的消息都逃不掉**。

被拦住的后果
------------
**静默丢弃，不报错。**

这是故意的：被限流时**不能再发一条"你被限流了"** —— 那本身就是一条消息。
而抛异常会让调用方出错甚至重试，更糟。
所以返回一个**看起来成功的空结果**，调用方正常继续。

每一次放行/拦截都写进 ``data/send_log.txt``，
**以后出问题能查到底是谁在刷、什么时候**。

.. important::
   这个插件**不做"最小发送间隔"**。因为 llmchat 的「分条」风格
   是一次回复分多条发送的，加间隔会把分条打断。
   频率控制用**滑动窗口**实现，它天然容忍"一批多条"。

配置（都在 ``.env``，**热读取，改完不用重启**）
------------------------------------------------
===========================================  ==========  ==================
键                                           默认        说明
===========================================  ==========  ==================
``SEND_GUARD_ENABLE``                        true        总开关
``SEND_GUARD_SILENT``                        false       **完全静默**（冷却期用）
``SEND_GUARD_GROUP_PER_MINUTE``              10          每群每分钟上限
``SEND_GUARD_GROUP_PER_HOUR``                120         每群每小时上限
``SEND_GUARD_GLOBAL_PER_MINUTE``             20          全局每分钟上限
``SEND_GUARD_MUTE_THRESHOLD``                10          ★每分钟被拦这么多次就熔断★
``SEND_GUARD_MUTE_SECONDS``                  600         熔断后静默多久
``SEND_GUARD_PRIVATE_PER_MINUTE``            15          私聊每分钟上限
``GROUP_WHITELIST_ENABLE``                   true        群白名单总开关（与入口插件共用）
``GROUP_WHITELIST``                          （空）      允许回复的群号，逗号分隔；**空 = 所有群都不回**
===========================================  ==========  ==================

**``SEND_GUARD_SILENT`` 是风控期的救命开关**：设成 true 后
bot 照常在线、照常接收消息（甚至照常调 AI），但**一条消息都不发出去**。
用来「让账号静养，同时保持在线」。
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import dotenv_values
from nonebot import get_driver, logger

#: 会被限流的 API（所有发消息的接口）
SEND_APIS = frozenset({
    "send_msg",
    "send_group_msg",
    "send_private_msg",
    "send_group_forward_msg",
    "send_private_forward_msg",
})

#: 假成功响应：被拦时返回它，让调用方以为发成功了（避免报错/重试）
_FAKE_OK: dict[str, Any] = {"status": "ok", "retcode": 0, "data": None, "wording": ""}

#: 滑动窗口长度（秒）。
#:
#: 提成模块常量是**为了可测** —— 测试里把它们改小，
#: 就能在毫秒级验证「窗口过期后自动恢复」，
#: 否则要真等 60 秒。生产环境用默认值。
MIN_WINDOW = 60.0
HOUR_WINDOW = 3600.0

_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
_LOG_PATH = Path(__file__).resolve().parent.parent / "data" / "send_log.txt"

#: 配置缓存（热读取但不必每次都读盘）
_cfg_cache: tuple[float, dict[str, str]] = (0.0, {})
_CFG_TTL = 5.0


def _env() -> dict[str, str]:
    """读 .env（5 秒缓存）。只读发送闸门自己的键，**不碰任何密钥**。"""
    global _cfg_cache
    now = time.time()
    if now - _cfg_cache[0] < _CFG_TTL and _cfg_cache[1]:
        return _cfg_cache[1]
    values: dict[str, str] = {}
    try:
        raw = dotenv_values(_ENV_PATH)
        for k, v in raw.items():
            if k and k.startswith("SEND_GUARD_") and v is not None:
                values[k] = v.strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[send_guard] 读 .env 失败：{}", exc)
    _cfg_cache = (now, values)
    return values


def _num(env: dict[str, str], key: str, default: float) -> float:
    try:
        return float(env.get(key, default))
    except (TypeError, ValueError):
        return default


def _flag(env: dict[str, str], key: str, default: bool) -> bool:
    raw = env.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "是"}


#: 群白名单缓存（与 ``plugins/group_whitelist.py`` 共用同一套 ``.env`` 键）
#:
#: 入口已经拦过一次（那边直接丢事件），这里只是**兜底**：
#: 万一有插件绕过 matcher 直接调 ``call_api``，也不能让它往不认识的群发东西。
_wl_cache: tuple[float, bool, set[str]] = (0.0, True, set())


def _whitelist_allow(group_id: int | str) -> bool:
    """群号是否在白名单内（配置热读取，5 秒缓存）。"""
    global _wl_cache
    now = time.time()
    if now - _wl_cache[0] >= _CFG_TTL:
        enabled, groups = True, set()
        try:
            raw = dotenv_values(_ENV_PATH)
            enabled = (raw.get("GROUP_WHITELIST_ENABLE") or "true").strip().lower() \
                not in {"0", "false", "no", "off", "否", ""}
            spec = (raw.get("GROUP_WHITELIST") or "")
            spec = spec.replace("，", ",").replace("、", ",").replace(" ", ",")
            groups = {p.strip() for p in spec.split(",") if p.strip().isdigit()}
        except Exception as exc:  # noqa: BLE001
            logger.warning("[send_guard] 读群白名单失败，按「全部不回复」处理：{}", exc)
            groups = set()
        _wl_cache = (now, enabled, groups)
    _, enabled, groups = _wl_cache
    if not enabled:
        return True
    return str(group_id) in groups


#: 发送日志超过这个大小就截掉前一半（防止无限增长）
_MAX_LOG_BYTES = 512 * 1024


def _log(line: str) -> None:
    """写发送日志（失败不影响功能）。

    超过 ``_MAX_LOG_BYTES`` 就**保留后半段** ——
    排查风控问题时最近的数据才有用，久远的没价值。
    """
    try:
        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        if _LOG_PATH.exists() and _LOG_PATH.stat().st_size > _MAX_LOG_BYTES:
            lines = _LOG_PATH.read_text(encoding="utf-8").splitlines()
            kept = lines[len(lines) // 2:]
            _LOG_PATH.write_text(
                "\n".join(kept) + "\n", encoding="utf-8"
            )
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with _LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(f"[{stamp}] {line}\n")
    except Exception:  # noqa: BLE001
        pass


class SendGate:
    """出站消息闸门。

    线程/协程安全：所有检查都在一把锁里做 —— 发送不频繁，锁开销可忽略，
    但能保证计数准确。
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        #: 每个会话（群/私聊）**实际发出**的时间戳
        self._hits: dict[str, deque[float]] = {}
        #: 每个会话**被拦下**的时间戳 —— ★熔断看这个★
        self._blocks: dict[str, deque[float]] = {}
        #: 全局发送时间戳
        self._global_hits: deque[float] = deque()
        #: 被熔断静音的会话 -> 解除时间
        self._muted: dict[str, float] = {}
        #: 统计
        self.sent = 0
        self.blocked = 0
        self.muted_count = 0

    def _prune(self, dq: deque[float], now: float, window: float) -> None:
        while dq and now - dq[0] > window:
            dq.popleft()

    async def check(self, api: str, data: dict[str, Any]) -> tuple[bool, str]:
        """判断这条消息能不能发。

        :return: ``(是否放行, 原因)``
        """
        env = _env()
        if not _flag(env, "SEND_GUARD_ENABLE", True):
            return True, "闸门关闭"

        if _flag(env, "SEND_GUARD_SILENT", False):
            return False, "完全静默模式（SEND_GUARD_SILENT=true）"

        # 会话标识：群消息用群号，私聊用用户号
        group_id = data.get("group_id")
        user_id = data.get("user_id")
        if group_id:
            # ⓪ 群白名单兜底（入口已拦过一次，这里防止绕过 matcher 的直接调用）
            if not _whitelist_allow(group_id):
                return False, f"群 {group_id} 不在白名单"
            key, is_group = f"g{group_id}", True
        elif user_id:
            key, is_group = f"u{user_id}", False
        else:
            # 认不出会话就别拦（可能是合并转发等），但记一笔
            return True, "无法识别会话"

        now = time.time()
        per_min = _num(
            env,
            "SEND_GUARD_GROUP_PER_MINUTE" if is_group else "SEND_GUARD_PRIVATE_PER_MINUTE",
            10 if is_group else 15,
        )
        per_hour = _num(env, "SEND_GUARD_GROUP_PER_HOUR", 120)
        global_per_min = _num(env, "SEND_GUARD_GLOBAL_PER_MINUTE", 20)
        mute_threshold = _num(env, "SEND_GUARD_MUTE_THRESHOLD", 10)
        mute_seconds = _num(env, "SEND_GUARD_MUTE_SECONDS", 600)

        async with self._lock:
            # ① 该会话是否在静音期
            until = self._muted.get(key)
            if until is not None:
                if now < until:
                    remain = int(until - now)
                    return False, f"该会话熔断中，还要静默 {remain} 秒"
                self._muted.pop(key, None)

            hits = self._hits.setdefault(key, deque())
            self._prune(hits, now, HOUR_WINDOW)
            self._prune(self._global_hits, now, MIN_WINDOW)

            # ★ 熔断的依据是「**被拦次数**」，不是「尝试次数」★
            #
            # 这个区别很关键，一开始写错了：
            #
            # * 用「尝试次数」→ **正常用户也会被误熔断**。
            #   群友问满 10 条配额后如果继续问几句，尝试数就冲过阈值，
            #   整个群被静默 10 分钟 —— 惩罚了一个正常说话的人。
            # * 用「被拦次数」→ 正常用户超配额后试一两次就放弃了，
            #   被拦数停在 1~2；而**刷的人会一直试**，被拦数暴涨。
            #
            # 所以：**一分钟内被拦 N 次 = 判定为刷**。
            blocks = self._blocks.setdefault(key, deque())
            self._prune(blocks, now, MIN_WINDOW)

            hits_last_min = sum(1 for t in hits if now - t <= MIN_WINDOW)

            # ② 各种限流检查（命中就记一次「被拦」）
            reason = ""
            if hits_last_min >= per_min:
                reason = f"该会话已达每分钟上限（{int(per_min)} 条）"
            elif len(hits) >= per_hour:
                reason = f"该会话已达每小时上限（{int(per_hour)} 条）"
            elif len(self._global_hits) >= global_per_min:
                reason = f"全局已达每分钟上限（{int(global_per_min)} 条）"

            if reason:
                blocks.append(now)
                self.blocked += 1
                blocked_min = len(blocks)

                # ③ 被拦太多次 → 这不是「话多」，是「有人在刷」
                if blocked_min >= mute_threshold:
                    self._muted[key] = now + mute_seconds
                    self.muted_count += 1
                    _log(f"★熔断★ {key} 一分钟内被拦 {blocked_min} 次，"
                         f"静默 {int(mute_seconds)} 秒")
                    return False, (
                        f"该会话反复超出限制（一分钟被拦 {blocked_min} 次），"
                        f"已静默 {int(mute_seconds)} 秒"
                    )
                return False, reason

            # 放行：记录
            hits.append(now)
            self._global_hits.append(now)
            self.sent += 1

        return True, "ok"

    async def handle_blocked(self, api: str, data: dict[str, Any], reason: str) -> None:
        """记录一次拦截（**只写日志**；计数在 ``check`` 里做）。"""
        target = data.get("group_id") or data.get("user_id") or "?"
        _log(f"拦截 {api} → {target}：{reason}")

    def block_fake_result(self, api: str) -> Any:
        """被拦时返回的假结果（让调用方以为成功）。"""
        return dict(_FAKE_OK)


gate = SendGate()


def _patch() -> bool:
    """把闸门挂到 ``Bot.call_api`` 上。返回是否成功。"""
    try:
        from nonebot.internal.adapter.bot import Bot
    except Exception as exc:  # noqa: BLE001
        logger.error("[send_guard] 找不到 Bot 类，闸门未生效：{}", exc)
        return False

    if getattr(Bot.call_api, "__send_guard__", False):
        return True  # 已经挂过

    original = Bot.call_api

    async def guarded_call_api(self, api: str, **data: Any) -> Any:
        if api in SEND_APIS:
            try:
                allowed, reason = await gate.check(api, data)
            except Exception as exc:  # noqa: BLE001
                # 闸门自己出错时**放行**（宁可能发出去，也不要整个 bot 哑掉）
                logger.warning("[send_guard] 检查异常，本次放行：{}", exc)
                allowed, reason = True, "闸门异常"

            if not allowed:
                await gate.handle_blocked(api, data, reason)
                logger.warning("[send_guard] 拦截 {} → {}：{}",
                               api, data.get("group_id") or data.get("user_id"), reason)
                return gate.block_fake_result(api)

        return await original(self, api, **data)

    guarded_call_api.__send_guard__ = True  # type: ignore[attr-defined]
    guarded_call_api.__wrapped__ = original  # type: ignore[attr-defined]
    Bot.call_api = guarded_call_api  # type: ignore[method-assign]
    return True


driver = get_driver()


@driver.on_startup
async def _on_startup() -> None:
    """启动时挂闸门，并打印当前策略。"""
    ok = _patch()
    env = _env()
    enabled = _flag(env, "SEND_GUARD_ENABLE", True)
    silent = _flag(env, "SEND_GUARD_SILENT", False)

    if not ok:
        logger.error("[send_guard] ★闸门挂载失败，限流未生效！★")
        return

    if silent:
        logger.warning(
            "[send_guard] ★★ 完全静默模式已开启 ★★ "
            "bot 在线但一条消息都不会发出去（SEND_GUARD_SILENT=true）"
        )
    elif enabled:
        logger.info(
            "[send_guard] 限流已生效：每群 %d 条/分、%d 条/时，全局 %d 条/分，"
            "一分钟被拦 %d 次则熔断静默 %d 秒",
            int(_num(env, "SEND_GUARD_GROUP_PER_MINUTE", 10)),
            int(_num(env, "SEND_GUARD_GROUP_PER_HOUR", 120)),
            int(_num(env, "SEND_GUARD_GLOBAL_PER_MINUTE", 20)),
            int(_num(env, "SEND_GUARD_MUTE_THRESHOLD", 10)),
            int(_num(env, "SEND_GUARD_MUTE_SECONDS", 600)),
        )
        _log("=" * 20 + " 闸门启动 " + "=" * 20)
        _log(f"策略：每群 {int(_num(env, 'SEND_GUARD_GROUP_PER_MINUTE', 10))} 条/分，"
             f"{int(_num(env, 'SEND_GUARD_GROUP_PER_HOUR', 120))} 条/时，"
             f"全局 {int(_num(env, 'SEND_GUARD_GLOBAL_PER_MINUTE', 20))} 条/分")
    else:
        logger.warning("[send_guard] 限流已关闭（SEND_GUARD_ENABLE=false）")

    _patch()  # 有些插件会在启动后替换方法，再挂一次确保生效


@driver.on_shutdown
async def _on_shutdown() -> None:
    """退出时把统计写进日志。"""
    _log(f"停机统计：放行 {gate.sent} 条，拦截 {gate.blocked} 条，"
         f"熔断 {gate.muted_count} 次")