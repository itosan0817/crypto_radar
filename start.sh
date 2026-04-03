#!/bin/bash
cd /home/yoshi/backend
pkill -f aerodrome_radar.py
pkill -f bribe_sniper.py
sleep 2

nohup ./venv/bin/python aerodrome_radar.py >> radar.log 2>&1 < /dev/null &
nohup ./venv/bin/python bribe_sniper.py >> bribe_sniper.log 2>&1 < /dev/null &

echo "Started both scripts."
