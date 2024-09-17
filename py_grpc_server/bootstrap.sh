#!/bin/bash
cp -r /botblitz/* /app
cd /app
# note: -u tells python *not* to buffer stdout. Without this, calls to print()
# that don't get flushed were being omitted from the logs. Disabling
# buffering isn't great for performance, but it ensures we get all the logs.
python3 -u server.py