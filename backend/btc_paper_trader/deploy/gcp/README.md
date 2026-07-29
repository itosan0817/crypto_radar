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

### Web ダッシュボード（読み取り専用）

`paper_state.json` と `paper_events.jsonl` をブラウザで確認できます。認証は付いていないため、**既定は localhost のみ**です。LAN から見る場合は `--host 0.0.0.0` とファイアウォールでポートを限定してください。

```bash
cd /path/to/crypto_radar/backend
./venv/bin/pip install -r btc_paper_trader/requirements-btc.txt
./venv/bin/python -m btc_paper_trader dashboard --host 127.0.0.1 --port 8765
```

ブラウザで `http://127.0.0.1:8765/` を開く。別プロセスのため `paper` ループと同時に動かせます。

チャート（値動き・エントリー/決済ポイント・資産推移）と取引履歴を表示します。ローソク足チャートの描画には unpkg.com の lightweight-charts を CDN 読み込みするため、閲覧するブラウザ側にインターネット接続が必要です。

#### systemd で常駐させる（初回のみ）

```bash
sudo cp btc_paper_trader/deploy/gcp/btc-dashboard.service /etc/systemd/system/
sudo sed -i "s|@DEPLOY_ROOT@|/home/あなたのユーザー/crypto_radar|g; s|@USER@|あなたのユーザー|g" /etc/systemd/system/btc-dashboard.service
sudo systemctl daemon-reload
sudo systemctl enable --now btc-dashboard
```

以後は GitHub Actions のデプロイが `btc-dashboard.service` も自動で再起動します（ユニット未導入のサーバーでは何もしません）。

デプロイの自動再起動には、ランナーユーザーがパスワードなしで `systemctl restart btc-dashboard.service` を実行できる必要があります（既存3サービスと同様）。未設定の場合は一度だけ:

```bash
echo "$(whoami) ALL=(ALL) NOPASSWD: $(command -v systemctl) restart btc-dashboard.service" | sudo tee /etc/sudoers.d/btc-dashboard
sudo chmod 440 /etc/sudoers.d/btc-dashboard
sudo -n systemctl restart btc-dashboard.service && echo OK   # パスワードを聞かれなければ成功
```

#### 設定変更と What-if シミュレーション

ダッシュボードの「設定」パネルから主要パラメータ（エントリー閾値・利確/損切り幅・投入資金割合など）を編集できます。

- **保存して本番へ反映**: `config.local.yaml` に書き込まれ、稼働中の paper ループへ最大 `paper.reload_runtime_params_seconds`（既定300秒）で自動反映されます。再起動不要。保存値は `runtime_params.json`（自動チューニング）より優先されます。
- **この設定で過去をシミュレート (What-if)**: 保存せずに、選択中の期間（1/3/7日）を仮設定で再シミュレートし、実績との比較表・チャートマーカー・資産推移の重ね描きを表示します。モデルを学習し直すため数十秒かかります。

- **Claudeに参考値を聞く**: 現在の設定と直近7日の成績（取引数・勝率・決済/見送り理由の内訳）を Claude CLI に渡し、各パラメータの推奨値と理由を取得して入力欄の横に表示します（チップをクリックすると入力欄へ反映）。モデルは `config.yaml` の `param_advisor.model`（既定 sonnet）。
- **円表示切替**: ツールバーの「円」ボタンで全金額表示（評価額・損益・建値・チャート軸）を日本円換算に切り替えられます。レートは frankfurter.app (ECB) の USD/JPY を6時間キャッシュで使用（USDT≒USDとみなした表示用の換算）。
- **時間足切替**: チャートは 1分/15分/1時間/4時間/日足/週足 を選択可能。キャッシュに無い・更新が止まっている足は Binance から直接補完します（DBには書き込まない）。
- **テクニカル指標**: SMA20 / EMA9 / EMA21 / ボリンジャーバンド(20,2σ) のオーバーレイと、RSI(14) または MACD(12,26,9) のサブチャート（価格チャートとズーム同期）。表示選択はブラウザに記憶されます。
- **取引履歴ページ** (`/trades`): 値動きチャートには売買ポイントを表示せず（見づらいため廃止）、取引履歴は別ウィンドウの専用ページで確認します。チャート下の「📋 取引履歴を別ウィンドウで開く」リンクから開けます。期間（今日/7日/30日/90日/180日/全期間/カスタム日付指定）・方向・決済理由での絞り込みとページネーションに対応し、スマートフォンでも見やすいレイアウトです。

#### Claude 自動チューニング（auto_tune）

`config.yaml` の `auto_tune.enabled: true` で、毎日の日次締め（00:00 UTC / 09:00 JST 頃）に以下を無人実行します:

1. 直近7日の成績を診断（勝率・PF・決済/見送り理由の内訳）
2. Claude（提案役）が候補パラメータセットを最大4個提案（1回の変更は現在値±25%まで）
3. 別の Claude（リスク審査役）が危険な候補を veto
4. 現行設定と各候補を直近14日の what-if バックテストで同条件比較
5. **現行に明確に勝った候補のみ** `config.local.yaml` に自動適用
6. 適用から2日後、実現PnLが初期資金の−2%を下回っていたら前回値へ自動ロールバック

結果は日次サマリ・Claude日次レビューと**1通のDiscordメッセージにまとめて**日次チャンネルへ届きます（「🔧 自動チューニング」欄）。

- 対象は戦略パラメータ6項目のみ。**投入資金割合・日次最大損失率・advisor.mode は対象外**（ダッシュボードから手動でのみ変更可能）
- 全実行履歴は `data/auto_tune_history.jsonl` に記録され、ダッシュボード下部の「Claude 自動チューニング履歴」でも確認できます
- 手動実行/検証: `./venv/bin/python -m btc_paper_trader autotune --dry-run`（適用せず判断だけ見る）
- 止めたい場合はダッシュボードには出ないため `config.yaml`（または `config.local.yaml` に `auto_tune: {enabled: false}`）で無効化

設定変更を保護したい場合は `backend/.env` に `DASHBOARD_TOKEN=任意の文字列` を設定してください。設定すると、保存・What-if 実行時に画面のトークン欄への入力が必要になります（閲覧は従来どおり制限なし）。

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
