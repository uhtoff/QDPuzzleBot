import bot
import asyncio
from bot.utils import config

if config.debug is True:
    import pydevd_pycharm
    pydevd_pycharm.settrace('192.168.44.2', port=12345, stdoutToServer=True, stderrToServer=True)

asyncio.run(bot.main())