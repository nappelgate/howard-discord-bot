#!/bin/bash
# Unset the XPC service context that launchd injects — it can interfere
# with asyncio's event loop and network stack on macOS Sequoia.
unset XPC_SERVICE_NAME
unset LAUNCH_DAEMON_SOCKET_ORDER
exec /Users/nathanappelgate/discord-chaos-bot/.venv/bin/python \
     /Users/nathanappelgate/discord-chaos-bot/bot.py
