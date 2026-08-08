import bot
import asyncio
from bot.utils import config

if config.debug is True:
    # The debugger is a *dev* dependency and is absent after deploy.sh's
    # `pipenv sync` (which installs [packages] only). Leaving "debug": true in a
    # production config used to crash the bot on import; warn and carry on instead.
    try:
        import pydevd_pycharm
    except ImportError:
        print(
            "[run] config debug=true but pydevd_pycharm is not installed "
            "(it is a dev-only dependency); starting without the debugger."
        )
    else:
        # Endpoint comes from config -- it is the *developer's* IDE, so there is no
        # sensible shared default and a hardcoded LAN address was wrong for everyone
        # but its author.
        pydevd_pycharm.settrace(
            config.debug_host,
            port=config.debug_port,
            stdoutToServer=True,
            stderrToServer=True,
            suspend=False,
        )

asyncio.run(bot.main())
