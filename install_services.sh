#!/bin/bash
sudo mv /home/yoshi/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo pkill -f aerodrome_radar.py
sudo pkill -f bribe_sniper.py
sudo pkill -f "python -m btc_paper_trader paper"
sleep 2

sudo systemctl enable aerodrome-radar.service
sudo systemctl enable bribe-sniper.service
sudo systemctl enable btc-paper-trader.service

sudo systemctl restart aerodrome-radar.service
sudo systemctl restart bribe-sniper.service
sudo systemctl restart btc-paper-trader.service

sudo systemctl status aerodrome-radar.service --no-pager
sudo systemctl status bribe-sniper.service --no-pager
sudo systemctl status btc-paper-trader.service --no-pager
