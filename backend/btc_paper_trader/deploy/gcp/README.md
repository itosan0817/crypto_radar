# Google Cloud VM での実行

ペーパー取引ループ（`python -m btc_paper_trader paper`）を常時動かすための手順です。

## 前提

- **VM OS**: Ubuntu 22.04 LTS 推奨（e2-small 以上推奨。`tune` や学習は CPU・メモリを使います）
- **ネットワーク**: 外向き **HTTPS（443）** で Binance API・Discord Webhook に届けばよい（インバウンド開放は不要）
- **リポジトリ**: このリポジトリを VM 上に `git clone` し、`backend` が存在するパスを控える

## 1. シークレット（`.env`）

VM 上で `backend/.env` を作成し、少なくとも次を設定します。

```env
DISCORD_WEBHOOK_URL_HOURLY=https://discord.com/api/webhooks/...
DISCORD_WEBHOOK_URL_DAILY=https://discord.com/api/webhooks/...
```

`.env` のパーミッション例: `chmod 600 backend/.env`

## 2. 仮想環境と依存関係

```bash
cd /path/to/crypto_radar/backend
python3 -m venv venv
./venv/bin/pip install -U pip
./venv/bin/pip install -r btc_paper_trader/requirements-btc.txt
```

初回は K 線キャッシュ用に:

```bash
./venv/bin/python -m btc_paper_trader fetch
```

## 3. systemd で常駐（推奨）

`deploy/gcp/install.sh` は venv 作成・依存インストール・ユニット配置まで行います。

```bash
export DEPLOY_ROOT="/home/あなたのユーザー/crypto_radar"
bash btc_paper_trader/deploy/gcp/install.sh
```

`btc-paper-trader.service` 内の **`User=` / `Group=`** を VM のユーザーに合わせて編集したうえで:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now btc-paper-trader
sudo systemctl status btc-paper-trader
```

ログ:

```bash
journalctl -u btc-paper-trader -f
```

## 4. 定期 `fetch` + `tune`（自動パラメータ更新）

グリッド探索で **TP/SL・`weight_model`・`entry_threshold`・`min_confidence`** を最後のウォークフォワード窓で評価し、改善時のみ `data/runtime_params.json` を更新します（`config.yaml` の `tune.skip_if_worse_than_current`）。Discord の **日次ウェブフック**に結果サマリが送られます（未設定なら送信されません）。

`crontab -e` の例（毎週日曜 03:00 UTC）:

```cron
0 3 * * 0 cd /path/to/crypto_radar/backend && ./venv/bin/python -m btc_paper_trader fetch && ./venv/bin/python -m btc_paper_trader tune
```

**`paper` への反映**: `config.yaml` の `paper.reload_runtime_params_seconds`（既定 300）ごとに `runtime_params.json` を再読み込みします。**再起動は不要**です。間隔を `0` にすると起動時のみ読み込み、その場合は `tune` 後に `sudo systemctl restart btc-paper-trader` が必要です。

```bash
sudo systemctl restart btc-paper-trader
```

### Windows タスク スケジューラ（開発マシン）

同様に `backend` をカレントにして `python -m btc_paper_trader fetch` と `python -m btc_paper_trader tune` を順に実行するタスクを登録する。ペーパーループは別プロセスで常時起動し、`reload_runtime_params_seconds` で新設定を取り込む。

## 4.5 無取引時の緩和テスト（デバッグ用）

「数時間取引ゼロ」が続く時は、まず以下で原因切り分けします。

1) 実行ログで新規バー処理を確認:

```bash
journalctl -u btc-paper-trader -f
```

毎時通知に `new_bars` / `signals` / `top_reasons` が出るようになっているため、
「バーが来ていない」のか「シグナルがブロックされた」のかを確認できます。

2) 条件を最大限緩和したテスト設定で単発実行:

```bash
cd /path/to/crypto_radar/backend
./venv/bin/python -m btc_paper_trader paper --config btc_paper_trader/config.test_loose.yaml --once
```

必要なら数回実行:

```bash
for i in {1..5}; do ./venv/bin/python -m btc_paper_trader paper --config btc_paper_trader/config.test_loose.yaml --once; sleep 5; done
```

緩和設定では `filters.enabled: false`、`entry_threshold: -1.0`、`use_runtime_params: false` で
取引が発生しやすい状態になります。

3) ログ確認:

```bash
tail -n 20 btc_paper_trader/data/paper_events.loose.jsonl
```

## 4.6 通常設定へ戻す（重要）

- 本番相当の常駐では **必ず通常 config** を使う:
  - systemd は `python -m btc_paper_trader paper`（`config.yaml`）を維持
- 緩和テスト用 state/log は必要に応じて削除:

```bash
rm -f btc_paper_trader/data/paper_state.loose.json btc_paper_trader/data/paper_events.loose.jsonl
```

## 5. トラブルシュート

| 現象 | 確認 |
|------|------|
| 起動直後に落ちる | `journalctl -u btc-paper-trader -n 50`、`.env` の有無、`WorkingDirectory` が `backend` か |
| Discord に届かない | 環境変数名、`EnvironmentFile` パス、Discord からの外向き 443 |
| メモリ不足 | VM サイズを上げるか、`paper.train_window_bars` を `config.local.yaml` で小さくする |

## 6. セキュリティ

- VM には **API キー不要**（公開 REST のみ）だが、**Discord ウェブフック URL は秘密**として扱う
- `.env` をリポジトリにコミットしない
