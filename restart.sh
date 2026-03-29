#!/bin/bash
# 旧プロセスを停止して最新版で再起動するスクリプト
pkill -f aerodrome_radar.py || true
sleep 2
cd /home/yoshi/backend
nohup /home/yoshi/backend/venv/bin/python -u /home/yoshi/backend/aerodrome_radar.py > /home/yoshi/backend/radar.log 2>&1 &
sleep 5
tail -n 15 /home/yoshi/backend/radar.log
