# Ghost-Printer A1 — オンデバイス セットアップ手順書 (Pi 5 版)

> **2026-05-04 更新:** 現行プロジェクトのメイン HW は **Orange Pi 3B (4GB)** に
> 変更されました。 OPi 3B で組む場合は **`SETUP_OPI3B.md`** を参照してください。
> 設計判断の背景は `specs/b2_opi3b_migration.md`。
>
> このドキュメントは Pi 5 で組む場合のリファレンスとして残しています。

**対象機材:** Raspberry Pi 5 (8GB) + USB Mic + W25Q128 SPI Flash
**想定所要時間:** 実働3〜5日（コマンド実行自体は約4時間、モデルDL/ビルドの待ち時間を含む）
**前提:** 母艦PC(macOS/Linux)で本リポジトリをクローンし、`CORTEX.bin` を生成済みであること。

---

## 目次

1. [Phase 1: HW組み立て](#phase-1-hw組み立て-30分)
2. [Phase 2: OS初期設定](#phase-2-os初期設定-30分)
3. [Phase 3: 開発環境導入](#phase-3-開発環境導入-半日)
4. [Phase 4: モデル配置](#phase-4-モデル配置-1-2時間)
5. [Phase 5: CORTEX.bin の SPI Flash 書き込み](#phase-5-cortexbin-の-spi-flash-書き込み)
6. [Phase 6: 統合動作確認 & systemd常駐化](#phase-6-統合動作確認--systemd常駐化)
7. [トラブルシュート](#トラブルシュート)

---

## Phase 1: HW組み立て (30分)

### 1.1 microSD へ Raspberry Pi OS を書き込み

母艦PCで **Raspberry Pi Imager** を起動。

- Device: **Raspberry Pi 5**
- OS: **Raspberry Pi OS Lite (64-bit) — Bookworm**
- Storage: microSDカード (A2 推奨、64GB)

「次へ」で **OS Customisation** を開き、以下を設定:

- Hostname: `ghost-printer`
- Username: `pi`, Password: 任意の強めのもの
- Wi-Fi SSID / Password
- Enable SSH: ✅（Password authentication で可）
- Locale: `Asia/Tokyo` / `en_US.UTF-8`

書き込み完了後、microSDをPiに挿入、電源投入、数分待って:

```bash
ssh pi@ghost-printer.local
```

### 1.2 W25Q128 を SPI0 に結線

ブレッドボード上で以下の通り結線:

| W25Q128 pin | Pi 5 GPIO (BCM) | Pi 5 物理 pin |
|-------------|-----------------|---------------|
| VCC (3.3V)  | 3V3             | 1 or 17       |
| GND         | GND             | 9 or 25       |
| CS (CE0)    | GPIO 8 (CE0)    | 24            |
| SO (MISO)   | GPIO 9          | 21            |
| WP          | 3V3 (プルアップ)| 1             |
| SCLK        | GPIO 11 (SCLK)  | 23            |
| SI (MOSI)   | GPIO 10         | 19            |
| HOLD        | 3V3 (プルアップ)| 17            |

**注意:** WP/HOLD は未使用時は必ず 3V3 にプルアップ。ぶら下げたままだと誤動作する。

### 1.3 USBマイク / スピーカー接続

USBマイクをPi 5のUSB 2.0ポートに接続（USB 3.0は干渉が多い）。
スピーカー不要なら省略可。

---

## Phase 2: OS初期設定 (30分)

```bash
# システム更新
sudo apt update && sudo apt upgrade -y

# タイムゾーン確認
timedatectl | grep "Time zone"

# SPI / I2C を有効化
sudo raspi-config nonint do_spi 0
sudo raspi-config nonint do_i2c 0

# 反映のため再起動
sudo reboot
```

再ログイン後、SPI デバイスが出ていることを確認:

```bash
ls -la /dev/spidev*
# /dev/spidev0.0, /dev/spidev0.1 が見えればOK
```

### 2.1 ZRAM スワップを 2GB 追加

llama.cpp推論中のメモリ余裕確保のため:

```bash
sudo apt install -y zram-tools
echo 'ALGO=zstd' | sudo tee -a /etc/default/zramswap
echo 'PERCENT=50' | sudo tee -a /etc/default/zramswap
sudo systemctl enable --now zramswap
free -h  # Swap 行が増えていること
```

### 2.2 pi ユーザーを各種グループに追加

```bash
sudo usermod -aG audio,spi,gpio,i2c,dialout pi
# ログアウト→再ログインで反映
exit
ssh pi@ghost-printer.local
groups  # audio spi gpio i2c が含まれていること
```

---

## Phase 3: 開発環境導入 (半日)

### 3.1 ビルドツール / 依存パッケージ

```bash
sudo apt install -y \
    build-essential git cmake pkg-config \
    python3-dev python3-venv python3-pip \
    libportaudio2 portaudio19-dev \
    libopenblas-dev libssl-dev \
    ffmpeg \
    alsa-utils
```

### 3.2 本リポジトリを配置

```bash
cd ~
git clone <あなたの非公開リポジトリURL> ghost-printer
cd ghost-printer
# もしくは、母艦PCから rsync で転送:
#   rsync -avz --exclude '__pycache__' --exclude 'pytest-cache-files-*' \
#     ghost-printer-a1/ pi@ghost-printer.local:~/ghost-printer/
```

### 3.3 Python venv + 依存導入

```bash
cd ~/ghost-printer
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip wheel setuptools
pip install -r requirements.txt
```

`webrtcvad` が wheel 不在でビルドに失敗したら:

```bash
sudo apt install -y python3-webrtcvad
# もしくはソースからビルド: pip install webrtcvad-wheels
```

### 3.4 whisper.cpp をビルド

```bash
cd ~/ghost-printer
git clone https://github.com/ggerganov/whisper.cpp.git
cd whisper.cpp
make -j$(nproc)
# バイナリ: ./main
```

### 3.5 llama.cpp をビルド（外部Ollamaを使う場合は不要）

```bash
cd ~/ghost-printer
git clone https://github.com/ggerganov/llama.cpp.git
cd llama.cpp
make -j$(nproc) LLAMA_OPENBLAS=1
```

---

## Phase 4: モデル配置 (1-2時間, 回線依存)

```bash
cd ~/ghost-printer
mkdir -p models

# --- Whisper tiny 日本語 ~ 75MB ---
cd whisper.cpp/models
bash ./download-ggml-model.sh tiny
# 日本語精度を上げるなら base か tiny.ja を使う:
#   bash ./download-ggml-model.sh tiny.en  (英語専用・軽量)
cd ~/ghost-printer

# --- Qwen2.5-1.5B-Instruct Q4_K_M ~ 1GB ---
# Hugging Face からダウンロード（ログイン不要のミラーを選ぶ）
wget -O models/qwen2.5-1.5b-q4.gguf \
  "https://huggingface.co/bartowski/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/Qwen2.5-1.5B-Instruct-Q4_K_M.gguf"

# --- 動作確認 ---
./whisper.cpp/main -m whisper.cpp/models/ggml-tiny.bin -f whisper.cpp/samples/jfk.wav
./llama.cpp/main -m models/qwen2.5-1.5b-q4.gguf -p "Hello" -n 32
```

---

## Phase 5: CORTEX.bin の SPI Flash 書き込み

### 5.1 母艦PCで CORTEX.bin を生成

母艦PC側（このリポジトリのクローン）で:

```bash
cd ghost-printer-a1
python cortex_manager.py   # data/CORTEX.bin が生成される
sha256sum data/CORTEX.bin  # ハッシュを控えておく
```

### 5.2 Piへ転送

```bash
# 母艦 → Pi
scp data/CORTEX.bin pi@ghost-printer.local:~/ghost-printer/data/CORTEX.bin
```

### 5.3 シミュレータで疎通確認（安全のため）

```bash
# Pi 上で（実機Flashは触らない）
cd ~/ghost-printer
source .venv/bin/activate
python flash_cortex.py data/CORTEX.bin --simulate
# => "Flashing (simulated): wrote N bytes, SHA256 OK"
```

### 5.4 実機 W25Q128 へ書き込み

```bash
# 実機書き込み
python flash_cortex.py data/CORTEX.bin
# => Erase sectors ... / Write pages ... / Verify SHA256 ... OK

# SHA256 読み返し検証
python flash_cortex.py --verify data/CORTEX.bin
# => "Verify OK: SHA256 matches"
```

失敗したら [トラブルシュート](#トラブルシュート) を参照。

---

## Phase 6: 統合動作確認 & systemd常駐化

### 6.1 単発動作テスト

```bash
# 6.1.1 Flashから CORTEX.bin を読み戻す
python main.py --load-cortex
# => "CORTEX loaded: version=1, ... sha256=..."

# 6.1.2 60秒マイク録音→SOUL更新（シミュレータでもOK）
python main.py --mic --duration 60 --simulate-ollama
# → 発話→SOULが更新されることを確認

# 6.1.3 Status表示
python main.py --status
# total_episodes が増えていることを確認
# Watch Point サマリも表示される

# 6.1.4 Watch Point 進化サイクル
python main.py --evolve
# => "Evolution tick: N watch points reviewed, M culled, K promoted"
```

### 6.2 systemd ユニット配置

```bash
sudo mkdir -p /home/pi/ghost-printer/logs
sudo chown -R pi:pi /home/pi/ghost-printer/logs

# サービスユニットをシステム側にリンク（編集のため copy を推奨）
sudo cp ~/ghost-printer/ghost-printer.service         /etc/systemd/system/
sudo cp ~/ghost-printer/ghost-printer-evolve.service  /etc/systemd/system/
sudo cp ~/ghost-printer/ghost-printer-evolve.timer    /etc/systemd/system/

sudo systemctl daemon-reload
```

### 6.3 起動 & 常駐化

```bash
# メインサービス
sudo systemctl enable --now ghost-printer.service

# 進化タイマー（6時間毎）
sudo systemctl enable --now ghost-printer-evolve.timer

# 状態確認
systemctl status ghost-printer.service
systemctl list-timers | grep ghost-printer

# ログ確認
journalctl -u ghost-printer.service -f
tail -f ~/ghost-printer/logs/ghost-printer.log
```

### 6.4 24時間観測テスト

そのまま24時間放置してから:

```bash
python main.py --status
# - total_episodes が増えているか
# - core_identity の μ が観測方向に動いているか
# - Watch Point が少なくとも1件生まれているか

# クラッシュチェック
systemctl is-active ghost-printer.service
journalctl -u ghost-printer.service --since "24 hours ago" | grep -i error
```

MVP 成功基準（ROADMAP.md 5節）を満たせばゴール。

---

## トラブルシュート

### SPI Flash が見つからない

```bash
# JEDEC ID 読み取りだけ試す
python flash_cortex.py --read-id
# 期待値: 0xEF 0x40 0x18 (Winbond W25Q128)
```

- `0x00 0x00 0x00` → 配線ミス、CS/CLK 入れ替え疑い
- `0xFF 0xFF 0xFF` → 3.3V が来ていない / WP,HOLD が未プルアップ
- そもそも `/dev/spidev0.0` が無い → `raspi-config` で SPI 未有効 → `sudo raspi-config nonint do_spi 0 && sudo reboot`

### USBマイクが認識されない

```bash
arecord -l          # カード番号を確認
arecord -D plughw:1,0 -f cd -d 5 test.wav   # 録音テスト
aplay test.wav                              # 再生（スピーカーある場合）
```

`sounddevice` から見えない場合:

```bash
python -c "import sounddevice; print(sounddevice.query_devices())"
# --device <ID> で main.py に明示指定
```

### llama.cpp推論が遅い / OOM

- `models/qwen2.5-0.5b-q4.gguf` にダウングレード（~350MB）
- `main.py` 内で `max_tokens` を 64→32 に下げる
- ZRAMが有効か `free -h` で確認

### systemd サービスが起動直後に落ちる

```bash
journalctl -u ghost-printer.service -n 100 --no-pager
# 典型原因:
#  - .venv のパスが違う → ExecStart を編集
#  - portaudio/alsa デバイス未準備 → Unit の After= に sound.target 追加済み
#  - SPI権限 → SupplementaryGroups=spi を確認、pi が spi グループに居るか確認
```

### soul.json が壊れた

```bash
# バックアップから復旧、無ければ初期化
cp data/soul.json data/soul.json.broken
python main.py --init   # まっさらな SOUL で再出発
# CORTEXはそのまま生きるので人格パラメータは保持される
```

---

## 参考: ディレクトリ構成（デプロイ後）

```
/home/pi/ghost-printer/
├── .venv/                     # Python 仮想環境
├── main.py                    # エントリポイント
├── mic_capture.py             # USBマイク取込＋VAD
├── flash_cortex.py            # W25Q128 書込
├── cortex_manager.py          # CORTEX.bin 読み書き
├── soul_cortex.py             # 3モデル協調
├── watchpoint.py              # 観測生態系
├── soul_schema.py / soul_engine.py / extractor.py
├── permission_gateway.py / capability_token.py
├── requirements.txt
├── ghost-printer.service         # systemd 本体
├── ghost-printer-evolve.service  # 進化ワンショット
├── ghost-printer-evolve.timer    # 6時間毎
├── whisper.cpp/                  # make済みバイナリ
├── llama.cpp/                    # make済みバイナリ (任意)
├── models/
│   ├── qwen2.5-1.5b-q4.gguf
│   └── ... (MiniLM等)
├── data/
│   ├── CORTEX.bin             # 母艦から転送 → Flashに焼く元
│   └── soul.json              # 成長するSOUL（頻繁に書換）
└── logs/
    ├── ghost-printer.log
    ├── ghost-printer.err.log
    └── evolve.log
```

---

**メンテナンス後の合言葉:** `systemctl status ghost-printer && python main.py --status`

困ったら `ROADMAP.md` の §5 成功基準と §6 リスク表を見返す。
