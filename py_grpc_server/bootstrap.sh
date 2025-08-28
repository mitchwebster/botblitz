#!/bin/bash
cp -r /botblitz/* /app/py_grpc_server
cd /app/py_grpc_server
# note: -u tells python *not* to buffer stdout. Without this, calls to print()
# that don't get flushed were being omitted from the logs. Disabling
# buffering isn't great for performance, but it ensures we get all the logs.
python3 -u server.py