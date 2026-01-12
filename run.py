import bot
import asyncio
import cProfile
from bot.utils import config

if config.debug is True:
    import pydevd_pycharm
    pydevd_pycharm.settrace('192.168.44.2', port=29781, stdout_to_server=True, stderr_to_server=True, suspend=False)

asyncio.run(bot.main())