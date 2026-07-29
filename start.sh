#!/bin/bash
cd /home/yoshi/backend
pkill -f aerodrome_radar.py
sleep 2

nohup ./venv/bin/python aerodrome_radar.py >> radar.log 2>&1 < /dev/null &

echo "Started."
