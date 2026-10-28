"""PJSK（世界计划）集成插件 —— 模块化数据层 + 业务层 + 指令层。

为什么这么分
------------
PJSK 的数据源很杂：有的走 GitHub 上的静态 master data，有的走第三方榜线 API，
有的只要纯计算。而且**各个源支持的区服不一样**（比如榜线只有日服有，
活动数据国服日服都有）。如果把这些判断散在指令里，以后加一个源就要改一遍指令。

所以分三层::

    providers/   数据源层：一个源一个文件，**自己声明支持的区服与能力**
    services/    业务层：查活动、算实效、查歌曲…… 指令和 AI 工具共用
    commands/    指令层：固定指令（查活动 / 实效 / 查歌曲 ……）
    tools.py     LLM 工具：让 AI 也能调用同一套 services

好处：**加数据源只加一个文件，加功能只加一个 service**，互不影响。

支持的区服
----------
``cn`` 国服、``jp`` 日服、``tw`` 台服、``kr`` 韩服、``en`` 国际服。
指令里可以写中文别名，例如「查活动 日服」「查活动 国服」。

全部基于公开数据，**不需要登录、不需要游戏账号、不需要抓包**。

配置
----
所有开关都在 ``.env`` 里，前缀 ``PJSK_``，改完重启生效。
详见 ``config.py``。
"""

from __future__ import annotations

import os

from nonebot import logger

from .config import cfg

# MCP 服务器进程（pjsk_mcp.py）只用 services，**不该注册指令**：那边没有
# NoneBot 运行时，而且 stdio 传输要求 stdout 是纯 JSON-RPC —— 万一触发了
# nonebot 的日志初始化，日志会打乱协议流。
_MCP_MODE = os.environ.get("PJSK_MCP_MODE") == "1"

if not _MCP_MODE:
    # 载入指令（on_command 在 import 时注册）
    from . import commands  # noqa: F401

__all__ = ["cfg"]

if not _MCP_MODE:
    logger.debug(
        "[pjsk] 已载入，默认区服 = %s，缓存目录 = %s", cfg.default_region, cfg.cache_dir
    )