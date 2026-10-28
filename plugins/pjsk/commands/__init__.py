"""指令层。

每个功能一个文件，指令在这里注册（``on_command``）。
指令只做三件事：**解析参数 → 调 service → 回复**。
真正的逻辑都在 ``services/``，这样 AI 工具才能复用同一套代码。

约定
----
* 参数第一位通常是区服（可写中文：``查活动 日服``），不写就用 ``.env`` 里的默认区服
* 权限：查询类指令**所有人可用**；管理类指令（清缓存）限超级用户
* 报错时回复人话，不抛栈

新增指令：写一个 ``commands/xxx.py``，然后在下面 import 进来即可。
"""

from __future__ import annotations

from . import border, card, deck, effect, event, misc, music  # noqa: F401  导入即注册指令

__all__ = ["border", "card", "deck", "effect", "event", "misc", "music"]