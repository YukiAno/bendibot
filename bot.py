"""
bendibot —— QQ 聊天机器人入口

技术栈：
    NapCat (OneBot v11 协议端)
        ↓ 反向 WebSocket
    NoneBot2 (本文件，框架端)
        ↓
    nonebot-plugin-llmchat (LLM 对话)

启动：
    .\\venv\\Scripts\\python.exe bot.py

配置：
    全部配置项位于同目录 .env（含密钥，已被 .gitignore 排除）
"""

import nonebot
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter

# 读取 .env 并初始化 NoneBot
nonebot.init()

# 注册 OneBot v11 适配器，用于对接 NapCat
driver = nonebot.get_driver()
driver.register_adapter(OneBotV11Adapter)

# 载入 pyproject.toml 中 [tool.nonebot] 声明的插件与插件目录
nonebot.load_from_toml("pyproject.toml")

# 内置 echo 插件：用来快速验证「NapCat → NoneBot」这条链路通不通。
# 用法：在群里 @机器人 并发送「/echo 你好」（注意它带了 to_me()，必须 @）。
#
# 为什么要在代码里手动加载，而不是靠 pyproject.toml 的 builtin_plugins：
#   本项目的 pyproject.toml 还是 NoneBot 的「旧格式」，启动时会打印
#   [WARNING] nonebot | Legacy project format found! Upgrade with `nb upgrade-format`.
#   旧格式下 builtin_plugins 这一项不生效，echo 不会被加载。
#   （将来如果执行了 nb upgrade-format 升级格式，builtin_plugins 就会生效，
#     此时这行会因「插件已存在」而报错——那时把下面这段删掉即可。）
if nonebot.get_plugin("echo") is None:
    nonebot.load_builtin_plugin("echo")

if __name__ == "__main__":
    # 中文提示交给 Python 打印：走 Windows Unicode 控制台接口，
    # 不受 cmd 代码页影响，永远不会乱码。
    print("=" * 58)
    print("  bendibot 启动中，请稍候……")
    print()
    print("  看到下面这一行，就说明启动成功了：")
    print("    Uvicorn running on http://127.0.0.1:8080")
    print()
    print("  启动成功后请保持本窗口开着（关掉就等于停止机器人）")
    print("  要停止运行：按 Ctrl+C")
    print("=" * 58)
    print()
    nonebot.run()