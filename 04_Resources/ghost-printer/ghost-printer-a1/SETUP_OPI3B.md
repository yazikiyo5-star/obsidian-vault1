# Ghost-Printer — Orange Pi 3B 実機セットアップ手順書

**対象機材:** Orange Pi 3B (4GB) + USB マイク + W25Q シリーズ SPI Flash (Pre-MVP は W25Q64)
**OS:** Armbian Bookworm Minimal (mainline kernel)
**所要時間:** 実働 1 日 (コマンドだけなら 2-3 時間、 モデル DL 待ちが大半)
**前提:**
- 母艦 (mac/Linux) で本リポジトリ clone 済み
- `python cortex_manager.py` で `data/CORTEX.bin` 生成済み
- microSD 64GB 入手済み (届き次第)

> Pi 5 で組む場合は旧 `SETUP.md` を参照。 設計判断の背景は `specs/b2_opi3b_migration.md` を参照。

---

## 目次

1. [Phase 0: 開封 & 物理確認](#phase-0-開封--物理確認-10分)
2. [Phase 1: microSD に Armbian を焼く](#phase-1-microsd-に-armbian-を焼く-30分)
3. [Phase 2: 初回ブート & SSH 開通](#phase-2-初回ブート--ssh-開通-30分)
4. [Phase 3: OS 初期設定 & SPI 有効化](#phase-3-os-初期設定--spi-有効化-30分)
5. [Phase 4: 開発環境 & Python 依存](#phase-4-開発環境--python-依存-1時間)
6. [Phase 5: AI モデル配置](#phase-5-ai-モデル配置-1-2時間-回線次第)
7. [Phase 6: W25Q シリーズ Flash 配線 & CORTEX 書込み](#phase-6-w25q-シリーズ-flash-配線--cortex-書込み-30分)
8. [Phase 7: 統合動作確認](#phase-7-統合動作確認-30分)
9. [Phase 8: systemd 常駐化](#phase-8-systemd-常駐化-15分)
10. [Phase 9: 24 時間観測 & ベンチ取得](#phase-9-24時間観測--ベンチ取得)
11. [トラブルシュート](#トラブルシュート)

---

## Phase 0: 開封 & 物理確認 (10分)

入手した OPi 3B が以下を持っていることを確認:

- [ ] 40-pin GPIO ヘッダ (基板上端、 ピンが立っている)
- [ ] USB-C 給電ポート (microHDMI の隣)
- [ ] HDMI ポート × 1
- [ ] USB 3.0 × 1 (青) + USB 2.0 × 2
- [ ] LAN (RJ-45)
- [ ] **microSD スロット (基板裏面)** — USB ポート群の真裏
- [ ] (任意) eMMC ソケット (基板裏面、 SD スロットの近く)
- [ ] WiFi アンテナ取付済み

電源は **まだ繋がない**。

---

## Phase 1: microSD に Armbian を焼く (30分)

### 1.1 イメージダウンロード

母艦 (mac) で:

```bash
# 母艦 mac
mkdir -p ~/opi3b-setup && cd ~/opi3b-setup
# Armbian 公式から OPi 3B 用 Bookworm Minimal を取得
# https://www.armbian.com/orangepi-3b/
# server / minimal を選ぶ (デスクトップ不要)
curl -LO https://dl.armbian.com/orangepi3b/Bookworm_current_minimal
xz -d Armbian_*_Orangepi3b_bookworm_*.img.xz
```

### 1.2 Etcher で書き込み

```bash
brew install --cask balenaetcher
# Etcher を開いて:
#   1. Flash from file → Armbian_*.img を選択
#   2. Select target → microSD (64GB)
#   3. Flash!
# 約 5-10 分
```

> Raspberry Pi Imager でも書ける (Custom OS → Use custom image)。

### 1.3 SSH 事前設定 (任意・モニタ無し起動向け)

書込み後の SD を母艦に挿し直すと `BOOT` パーティションが見える:

```bash
# /Volumes/BOOT or /Volumes/armbian-boot
cd /Volumes/armbian-boot
# armbianEnv.txt に以下を追記して SPI を最初から有効化
echo 'overlays=spi-spidev' | sudo tee -a armbianEnv.txt
echo 'param_spidev_spi_bus=3' | sudo tee -a armbianEnv.txt
```

(オーバーレイ名は版で揺れる可能性あり。 動かなかったら Phase 3.2 で armbian-config から有効化)

### 1.3.1 ヘッドレス Wi-Fi setup (LAN ケーブル / HDMI モニタ / USB キーボード が無い場合)

**追加日: 2026-05-05** (qvp 承認、 spec b2 §3.2 注釈と整合)。 §1.3 の SPI 事前設定だけでは初回ブートで Wi-Fi に繋がらず SSH も開通しない。 LAN・HDMI も無い構成 (例: スマホテザリングのみ) で進める場合は、 SD の `/boot/` パーティションに **first-run wizard を自動完了させるカスタマイズファイル** を仕込む。

#### A. 検証順序 (Trixie で動作確認していない仕様なので必ずこの順で確認)

```bash
# /Volumes/BOOT (or 焼いた直後の Mac でマウントされた boot パーティション名) に移動
cd /Volumes/<boot 名>

# Step 1: テンプレートが存在するか
ls -la armbian_first_run.txt* armbian-firstrun-* 2>/dev/null
# 期待: armbian_first_run.txt.template があるはず
# Trixie でテンプレ名が変わっている可能性あり → ls で実在名を確認
```

#### B. テンプレートがある場合

```bash
# /boot/armbian_first_run.txt にコピー → エディタで以下を編集
cp armbian_first_run.txt.template armbian_first_run.txt

# 編集すべきキー (Pre-MVP では以下を最低限有効化):
#   Hostname=ghost-printer-a1
#   Network_WirelessAdapter=true
#   Network_ESSID="<テザリング SSID>"
#   Network_WPA="<テザリング パスワード>"
#   Network_Country_Code=JP
#   Authentication_Autologin=false
#   Username=haru
#   UserPassword=<相棒のお好み>
#   RootPassword=<相棒のお好み>
#   Locales=en_US.UTF-8
#   Timezone=Asia/Tokyo
```

#### C. テンプレートが無い場合 (Trixie で名前変更されているケース)

候補ファイル名 (どれかがある可能性):
- `/boot/armbian_first_run_config.txt`
- `/boot/firstrun.sh`
- `/boot/extlinux/armbian-firstrun.conf`

それでも見つからなければ、 NetworkManager の `nmconnection` を **直接 ROOT パーティション (ext4) に書く必要がある**が、 Mac は ext4 を読み書きできない (FUSE+macFUSE 等の追加ソフトが必要)。 この場合は:

- **(ア) HDMI モニタ + USB キーボードを借りる/買って初回 TUI で進む** ← 最も確実
- **(イ) Linux PC や Linux ライブ USB を一時的に使って ext4 に書く** ← Linux 環境が手配できれば
- **(ウ) USB→Ethernet アダプタを買って有線経由で SSH** ← LAN ケーブル + アダプタの実費

#### D. SSH キー事前注入 (任意)

`/boot/` パーティションに以下を置くと、 first-run 時に root の `~/.ssh/authorized_keys` に注入される (Armbian の慣例、 Trixie で動くか要検証):

```bash
# 母艦 Mac で
cat ~/.ssh/id_ed25519.pub  # or id_rsa.pub
# 出力をコピーして /Volumes/<boot 名>/authorized_keys に保存
```

これでパスワード認証無しで SSH できる。

#### E. 仕込みの確認

```bash
# Mac から SD eject 前に内容確認
cat /Volumes/<boot 名>/armbian_first_run.txt | head -40
ls /Volumes/<boot 名>/authorized_keys
diskutil eject /dev/disk8
```

#### F. 起動後の IP 取得方法

ヘッドレス起動なので OPi の IP は画面に出ない。 取得手段:

- **iPhone テザリング**: 「設定 → インターネット共有」で接続中デバイスが見えるが詳細 IP は出ない場合あり → Mac 側で `arp -a | grep -i 'incomplete\|orange\|armbian\|ghost'` を実行して同セグメントのホストを列挙
- **Android テザリング**: 端末によっては「接続デバイス」一覧で IP まで見える
- **Mac の bonjour**: `ping -c 3 ghost-printer-a1.local` または `dns-sd -G v4 ghost-printer-a1.local`
- **nmap スキャン** (高速): `brew install nmap` 後 `nmap -sn 172.20.10.0/28` (iPhone) または `nmap -sn 192.168.43.0/24` (Android 一般)

---

## Phase 2: 初回ブート & SSH 開通 (30分)

### 2.1 物理結線

1. microSD を OPi 3B 基板裏のスロットに挿入
2. LAN ケーブルを接続 (Wi-Fi より楽)
3. (初回のみ推奨) HDMI モニタ + USB キーボード接続
4. USB-C 電源を投入

緑/青 LED が点滅 → 起動。 1-2 分で初期セットアップ画面 (TUI) が出る。

### 2.2 初期ユーザ作成 (TUI)

- Root password 設定
- ログイン名: **`haru`** (memory のユーザ名に合わせる)
- パスワード設定
- Locale: `Asia/Tokyo`、 `en_US.UTF-8`
- WiFi セットアップ (LAN 派なら skip)

完了したらログインプロンプトが出る。

### 2.3 IP アドレスを確認 → SSH に切替

```bash
# OPi 3B 上で
ip addr show eth0   # 192.168.x.x をメモ
hostname -I
```

母艦から:

```bash
# 母艦
ssh haru@<OPi の IP>
# or hostname.local 経由 (mDNS が効いていれば)
ssh haru@orangepi3b.local
```

以後は SSH のみで作業。

---

## Phase 3: OS 初期設定 & SPI 有効化 (30分)

> **2026-05-05 実機検証注釈** (qvp 承認、 Trixie 26.2.0-trunk.843 / kernel 6.18.26 で実施):
> - **§3.2 SPI**: 旧 `overlays=spi-spidev` は kernel 6.18 では存在せず、 `overlays=spi3-m0-cs0-spidev` + `overlay_prefix=rockchip-rk3566` が正解 (実ファイル `rockchip-rk3566-spi3-m0-cs0-spidev.dtbo` と整合)。 詳細は §3.2.
> - **§3.3 ZRAM**: Armbian community Trixie image は `armbian-zram-config` で 2GB の zram を **既に組み込み起動** している。 `zram-tools` の `zramswap.service` は同じ /dev/zram0 を取り合って失敗する。 → `zram-tools` 自体を入れず、 もしくは入れたら `zramswap.service` を mask する。 詳細は §3.3.
> - **§3.4 groups**: Trixie ベースイメージは `spi`/`gpio`/`i2c` グループを **デフォルトで作成しない**。 `usermod -aG` の前に `groupadd -f` で先に作る必要あり。 さらに `/dev/spidev*` `/dev/gpiochip*` `/dev/i2c-*` のグループ所有権を割り当てる udev ルールも必要。 詳細は §3.4.

### 3.1 システム更新

```bash
# OPi 3B 上で
sudo apt update && sudo apt upgrade -y
sudo apt install -y curl git build-essential vim

# タイムゾーン確認
timedatectl status | grep "Time zone"
# Asia/Tokyo になっていなければ:
sudo timedatectl set-timezone Asia/Tokyo
```

### 3.2 SPI を有効化

**Trixie / kernel 6.18 で検証済みの手順 (推奨):**

```bash
# armbianEnv.txt に SPI3 overlay を追記
sudo cp /boot/armbianEnv.txt /boot/armbianEnv.txt.bak.$(date +%Y%m%d-%H%M%S)

# overlay_prefix を実ファイル名に整合させる (Trixie で rk35xx → rockchip-rk3566)
sudo sed -i 's/^overlay_prefix=.*/overlay_prefix=rockchip-rk3566/' /boot/armbianEnv.txt

# overlay とパラメータ追記
grep -q '^overlays=' /boot/armbianEnv.txt || \
    echo 'overlays=spi3-m0-cs0-spidev' | sudo tee -a /boot/armbianEnv.txt
grep -q '^param_spidev_spi_bus=' /boot/armbianEnv.txt || \
    echo 'param_spidev_spi_bus=3' | sudo tee -a /boot/armbianEnv.txt

cat /boot/armbianEnv.txt
```

または `armbian-config` の TUI でも可 (System → Hardware)。 ただし Trixie の TUI は項目が再構成されている場合があるので、 上記の手動編集が確実。

> **採択履歴注釈**: spec b2 §3.2 旧採択 `overlays=spi-spidev` は kernel 5.x / Bookworm の旧名。 kernel 6.18 では overlay ファイル `/boot/dtb/rockchip/overlay/rockchip-rk3566-spi3-m0-cs0-spidev.dtbo` が実体で、 `overlay_prefix` と組み合わせて `<prefix>-<overlays>.dtbo` で参照される。 採択精神 = "SPI3 を /dev/spidev3.0 として haru ユーザから読める形で出す" は完全に維持。

```bash
sudo reboot
```

再ログイン後:

```bash
ls -la /dev/spidev*
# 期待: crw-rw---- 1 root spi  153, 0  ... /dev/spidev3.0
# 出てこない場合は dmesg | grep -iE 'spi|overlay|dtbo' で原因確認
```

### 3.3 ZRAM スワップ (Armbian 組み込みを使う)

> **重要 (Trixie 検証 2026-05-05)**: Armbian community の Trixie image は `armbian-zram-config` ですでに 2GB zram swap を起動している (`cat /proc/swaps` で /dev/zram0 が見える)。 `zram-tools` を入れて `zramswap.service` を起動すると Device or resource busy で失敗する。 → 既存の Armbian 組み込みをそのまま使うのが正解。

```bash
# 既に zram swap が動いていることを確認
cat /proc/swaps
# 期待: /dev/zram0 partition 2005848 0 5

# zram swap が active であることを確認
free -h
# 期待: Swap line に約 1.9-2.0 Gi

# Armbian の zram-config が活きているか
systemctl is-active armbian-zram-config
```

**もし誤って `zram-tools` を入れてしまった場合は mask する:**

```bash
sudo systemctl stop zramswap.service 2>/dev/null
sudo systemctl disable zramswap.service 2>/dev/null
sudo systemctl mask zramswap.service
# → 以後は Armbian の組み込みのみが zram を管理
```

### 3.4 ユーザを必要グループに追加 + udev ルール

> **重要 (Trixie 検証 2026-05-05)**: Trixie ベースの Armbian image は `spi`/`gpio`/`i2c` グループを **作成しない**。 `usermod -aG spi haru` だけでは "グループ 'spi' は存在しません" で失敗する。 → 先にグループを作成し、 udev ルールで /dev/spidev* /dev/gpiochip* /dev/i2c-* のグループ所有権も付与する。

```bash
# 1) 不在グループを idempotent に作成
sudo groupadd -f spi
sudo groupadd -f gpio
sudo groupadd -f i2c

# 2) haru を必要グループに追加 (audio/dialout は既存)
sudo usermod -aG audio,spi,gpio,i2c,dialout haru

# 3) udev: SPI/GPIO/I2C デバイスにグループ・モードを割り当てる
sudo tee /etc/udev/rules.d/90-ghost-printer-perms.rules > /dev/null << 'UDEV_EOF'
# Ghost-Printer: device permissions for SPI / GPIO / I2C
KERNEL=="spidev[0-9]*.[0-9]*", GROUP="spi",  MODE="0660"
KERNEL=="gpiochip*",            GROUP="gpio", MODE="0660"
SUBSYSTEM=="i2c-dev",           GROUP="i2c",  MODE="0660"
UDEV_EOF
sudo udevadm control --reload-rules
sudo udevadm trigger

# 4) reboot して反映
sudo reboot
```

再ログイン後:

```bash
id -nG
# 期待: 末尾付近に spi gpio i2c が含まれる

ls -la /dev/spidev*
# 期待: crw-rw---- 1 root spi  ...  /dev/spidev3.0
```

---

## Phase 4: 開発環境 & Python 依存 (1時間)

### 4.1 ビルドツール / OS 依存

```bash
sudo apt install -y \
    python3-dev python3-venv python3-pip \
    cmake pkg-config \
    libportaudio2 portaudio19-dev \
    libopenblas-dev libssl-dev \
    ffmpeg alsa-utils \
    git
```

### 4.2 リポジトリ転送

母艦から rsync (推奨):

```bash
# 母艦
rsync -avz --exclude '__pycache__' --exclude '.pytest_cache' \
  --exclude '.venv' --exclude 'data/CORTEX.bin' \
  /path/to/Ghost-print/ghost-printer-a1/ \
  haru@<OPi>:~/ghost-printer/
```

または OPi で git clone:

```bash
# OPi
cd ~
git clone <あなたのリポジトリURL> ghost-printer
```

### 4.3 Python venv + 依存導入

```bash
# OPi
cd ~/ghost-printer
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip wheel setuptools
pip install -r requirements.txt
```

`webrtcvad` のビルドに失敗したら:

```bash
sudo apt install -y python3-webrtcvad
# または
pip install webrtcvad-wheels
```

### 4.4 自己テスト (HW 不要のもの)

```bash
cd ~/ghost-printer
source .venv/bin/activate
python -m pytest test_soul_binary.py test_soul_log.py \
  test_permission_gateway_binary.py test_soul_storage.py \
  test_merkle_proof.py -q
# → 98 件全通過すれば Track A6 が OPi 上で動いている
```

### 4.5 whisper.cpp / llama.cpp ビルド

```bash
cd ~/ghost-printer

# whisper.cpp
git clone https://github.com/ggerganov/whisper.cpp.git
cd whisper.cpp && make -j$(nproc) && cd ..

# llama.cpp
git clone https://github.com/ggerganov/llama.cpp.git
cd llama.cpp && make -j$(nproc) LLAMA_OPENBLAS=1 && cd ..
```

ビルド時間: OPi 3B で whisper.cpp 約 5 分、 llama.cpp 約 10 分。

---

## Phase 5: AI モデル配置 (1-2時間, 回線次第)

```bash
cd ~/ghost-printer
mkdir -p models

# Whisper tiny 日本語
cd whisper.cpp/models
bash ./download-ggml-model.sh tiny
cd ~/ghost-printer

# Bonsai 1.7B Q4_K_M (推奨) または Qwen2.5-0.5B (フォールバック)
# Bonsai は HuggingFace ホスティングを使う (URL は要確認)
wget -O models/bonsai-1.7b-q4.gguf \
  "https://huggingface.co/<bonsai-1.7b-gguf-repo>/resolve/main/Bonsai-1.7B-Q4_K_M.gguf"

# 動作確認
./whisper.cpp/main -m whisper.cpp/models/ggml-tiny.bin -l ja \
  -f whisper.cpp/samples/jfk.wav

./llama.cpp/main -m models/bonsai-1.7b-q4.gguf \
  -p "今日は良い一日だった" -n 32
# → トークン/秒が表示される。 OPi 3B で 5-10 t/s が目安
```

`time` を頭に付けて実測値を取り、 `specs/b2_opi3b_migration.md §2.2` の予測欄を実測値で書き換える。

---

## Phase 6: W25Q シリーズ Flash 配線 & CORTEX 書込み (30分)

> **Pre-MVP 採択チップ (2026-05-17):** **W25Q64FVAIG DIP-8** (aitendo 19957、 ¥209、 在庫 204点)。 W25Q128 国内品薄により変更。 W25Q シリーズはピン配置と SPI コマンドセットが共通なのでこの章の手順はそのまま使える。 `specs/b2_opi3b_migration.md §10` 注釈、 memory `project_ghost_printer_flash_chip_swap.md` 参照。

### 6.1 物理配線 (電源 OFF で実施)

W25Q64 DIP-8 (切欠きを左上に向けて) のピン配置:

```
        ┌──┐
   /CS ─┤1 8├─ VCC
    DO ─┤2 7├─ /HOLD
   /WP ─┤3 6├─ CLK
   GND ─┤4 5├─ DI
        └──┘
   ↑切欠き
```

| DIP-8 ピン | OPi 3B 物理ピン | 備考 |
|-----------|-----------------|------|
| 8 VCC     | 1 (3V3)         | **5V 厳禁** |
| 4 GND     | 9               | |
| 1 /CS     | 24              | SPI3_CS0_M0 |
| 2 DO (MISO) | 21            | SPI3_MISO_M0 |
| 3 /WP     | 17 (3V3)        | プルアップ必須 |
| 6 CLK     | 23              | SPI3_CLK_M0 |
| 5 DI (MOSI) | 19            | SPI3_MOSI_M0 |
| 7 /HOLD   | 17 (3V3)        | プルアップ必須 |

ブレッドボードを使う場合は、 W25Q64 を切欠きを左上にして挿し、 ブレッドボード右側の電源レール (赤) を OPi 3B の **ピン 1 (3.3V)** に繋いで、 VCC/WP/HOLD はそこから分配するのが楽。

`specs/b2_opi3b_migration.md §4.1` も参照。

### 6.2 母艦で CORTEX.bin 生成 → OPi へ転送

```bash
# 母艦
cd ghost-printer-a1
python cortex_manager.py
sha256sum data/CORTEX.bin

scp data/CORTEX.bin haru@<OPi>:~/ghost-printer/data/CORTEX.bin
```

### 6.3 シミュレータで疎通確認

```bash
# OPi
cd ~/ghost-printer && source .venv/bin/activate
python flash_cortex.py data/CORTEX.bin --simulate
# → "Flashing (simulated): wrote N bytes, SHA256 OK"
```

### 6.4 実機書込み

```bash
# OPi (電源 ON, W25Q64 結線済)
python flash_cortex.py data/CORTEX.bin --bus 3
# → Erase / Write / Verify SHA256 OK

python flash_cortex.py --verify data/CORTEX.bin --bus 3
# → "Verify OK"
```

> `--bus 3` 引数は B3 タスクで追加予定。 既存 `flash_cortex.py` が `spidev0.0` 決め打ちの場合は `flash_cortex.py` 内の `SpiDev().open(0, 0)` を `(3, 0)` に手動変更してから実行。

---

## Phase 7: 統合動作確認 (30分)

```bash
cd ~/ghost-printer && source .venv/bin/activate

# 7.1 SPI Flash から CORTEX を読み戻す
python main.py --load-cortex
# → "CORTEX loaded: version=1, sha256=..."

# 7.2 ShadowStorage で SOUL を初期化 (新 A6 経路)
python -c "
from soul_storage import ShadowStorage
from soul_schema import create_empty_soul
import os
master = os.urandom(32)
with open('data/master.key', 'wb') as f: f.write(master)
store = ShadowStorage(
    json_path='data/soul.json',
    bin_path='data/soul.bin',
    log_path='data/soul.log',
    master_key=master,
)
store.save_full(create_empty_soul('haru'))
print('initialized: bin =', os.path.getsize('data/soul.bin'), 'B')
"

# 7.3 マイク 60 秒キャプチャ → SOUL 更新
python main.py --mic --duration 60
# → 発話を抽出 → ShadowStorage で .soul + .soul.log + .json 更新

# 7.4 Status
python main.py --status
# total_episodes / Watch Point / core_identity の動きを確認

# 7.5 進化サイクル
python main.py --evolve
```

---

## Phase 8: systemd 常駐化 (15分)

### 8.1 ユーザを haru に書換

```bash
sudo sed -i 's/User=pi/User=haru/g' \
    ~/ghost-printer/ghost-printer.service \
    ~/ghost-printer/ghost-printer-evolve.service
```

### 8.2 自動再起動を効かせるユニット書換 (重要)

`ghost-printer.service` の `[Service]` セクションに以下が含まれることを確認:

```ini
[Service]
Type=simple
User=haru
WorkingDirectory=/home/haru/ghost-printer
ExecStart=/home/haru/ghost-printer/.venv/bin/python main.py --daemon
StandardOutput=append:/home/haru/ghost-printer/logs/ghost-printer.log
StandardError=append:/home/haru/ghost-printer/logs/ghost-printer.err.log

# ── 自動再起動 (重要) ────────────────────────────────
Restart=always
RestartSec=30s
WatchdogSec=120s            # 2 分応答なしで restart (要 sd_notify)
StartLimitBurst=5           # 5 分に 5 回まで restart 可
StartLimitIntervalSec=300

# 権限とリソース
SupplementaryGroups=spi audio gpio
MemoryMax=2G                # OOM 保護
CPUWeight=100

[Install]
WantedBy=multi-user.target
```

> `WatchdogSec` を効かせるには Python 側で `systemd.daemon.notify("WATCHDOG=1")` を 60 秒以内に呼ぶ必要あり。 未対応なら `WatchdogSec` 行は削除。

### 8.3 LLM プロセス用の独立 unit (推奨: `bonsai.service`)

llama.cpp / ollama を別 systemd unit にして、 ghost-printer 本体と独立に
restart 可能にする。 これで `LlmRestartManager` が `systemctl restart bonsai.service`
を叩いて LLM だけを再起動できる。

```ini
# /etc/systemd/system/bonsai.service
[Unit]
Description=Bonsai 1.7B llama.cpp server
After=network.target

[Service]
Type=simple
User=haru
WorkingDirectory=/home/haru/ghost-printer
ExecStart=/home/haru/ghost-printer/llama.cpp/server \
    -m /home/haru/ghost-printer/models/bonsai-1.7b-q4.gguf \
    --port 8080 --ctx-size 1024 --threads 4
Restart=always
RestartSec=10s
StartLimitBurst=10
StartLimitIntervalSec=600
MemoryMax=2G

[Install]
WantedBy=multi-user.target
```

`haru` ユーザに `systemctl restart bonsai.service` を許可するため sudoers 設定:

```bash
sudo visudo -f /etc/sudoers.d/ghost-printer
# 以下を追加:
haru ALL=(root) NOPASSWD: /usr/bin/systemctl restart bonsai.service
haru ALL=(root) NOPASSWD: /usr/bin/systemctl status bonsai.service
```

### 8.4 LLM 健康プローブ + 自動再起動の timer (推奨)

`ghost-printer-probe.timer` を 1 時間毎に走らせて、 LLM の健康確認と
DEAD 永続時の再起動を自動化する。

```ini
# /etc/systemd/system/ghost-printer-probe.service
[Unit]
Description=Ghost-Printer LLM health probe + auto-restart
After=ghost-printer.service bonsai.service

[Service]
Type=oneshot
User=haru
WorkingDirectory=/home/haru/ghost-printer
ExecStart=/home/haru/ghost-printer/.venv/bin/python main.py --probe-llm

# /etc/systemd/system/ghost-printer-probe.timer
[Unit]
Description=Hourly LLM health probe

[Timer]
OnBootSec=10min
OnUnitActiveSec=1h
Unit=ghost-printer-probe.service

[Install]
WantedBy=timers.target
```

`main.py --probe-llm` の中で:

```python
# 概念実装
from watchpoint_llm import LlmRestartManager, probe_llm_health
ok = probe_llm_health(proposer)
if not ok:
    restarter = LlmRestartManager(
        restart_command=["sudo", "systemctl", "restart", "bonsai.service"],
        health=proposer.health,
        min_dead_minutes=5.0,
        max_restarts_per_hour=3,
        cooldown_minutes=10.0,
    )
    attempt = restarter.maybe_restart()
    if attempt:
        time.sleep(30)              # bonsai が起き上がる時間
        probe_llm_health(proposer)  # 再 probe
```

### 8.5 配置 & 起動

```bash
# 配置
sudo cp ~/ghost-printer/ghost-printer.service          /etc/systemd/system/
sudo cp ~/ghost-printer/ghost-printer-evolve.service   /etc/systemd/system/
sudo cp ~/ghost-printer/ghost-printer-evolve.timer     /etc/systemd/system/
sudo cp ~/ghost-printer/ghost-printer-probe.service    /etc/systemd/system/  # 任意
sudo cp ~/ghost-printer/ghost-printer-probe.timer      /etc/systemd/system/  # 任意
sudo cp ~/ghost-printer/bonsai.service                 /etc/systemd/system/  # 推奨
sudo systemctl daemon-reload

# 起動
sudo systemctl enable --now bonsai.service
sudo systemctl enable --now ghost-printer.service
sudo systemctl enable --now ghost-printer-evolve.timer
sudo systemctl enable --now ghost-printer-probe.timer  # 任意

# 確認
systemctl status ghost-printer.service bonsai.service
systemctl list-timers | grep ghost-printer
journalctl -u ghost-printer.service -f
```

### 8.6 自動再起動の動作確認

```bash
# bonsai を意図的にクラッシュさせて自動復帰を確認
sudo systemctl kill bonsai.service
# 数秒待つ
sleep 15
systemctl is-active bonsai.service
# → "active (running)" が返ればOK

# ghost-printer 本体のクラッシュ復帰
sudo systemctl kill ghost-printer.service
sleep 35   # RestartSec=30s
systemctl is-active ghost-printer.service

# probe による再起動の動作確認 (dry-run)
GPP_RESTART_DRY_RUN=1 python main.py --probe-llm
```

---

## Phase 9: 24時間観測 & ベンチ取得

24 時間放置後:

```bash
# 1. クラッシュなし確認
systemctl is-active ghost-printer.service

# 2. SOUL 成長確認
python main.py --status

# 3. ベンチ実測 (specs/b2_opi3b_migration.md §8 参照)
sysbench cpu run --threads=4
time ./whisper.cpp/main -m ggml-tiny.bin -l ja -f sample.wav
time ./llama.cpp/main -m bonsai-1.7b-q4.gguf -p "test" -n 100

# 4. 結果を specs/b2_opi3b_migration.md §2.2 に書き戻す
```

ROADMAP §5 の MVP 成功基準を満たせばゴール。

---

## トラブルシュート

### `/dev/spidev3.0` が出てこない

```bash
# オーバーレイ確認
cat /boot/armbianEnv.txt | grep -E 'overlay|spi'
# 何も出ないなら:
echo 'overlays=spi-spidev' | sudo tee -a /boot/armbianEnv.txt
echo 'param_spidev_spi_bus=3' | sudo tee -a /boot/armbianEnv.txt
sudo reboot

# それでもダメなら別オーバーレイ名を試す
ls /boot/dtb/rockchip/overlay/ | grep spi
# 例: rk3566-spi3-m0-cs0-spidev
sudo nano /boot/armbianEnv.txt
# overlays= の右側を該当名に置換
```

### W25Q シリーズ Flash が JEDEC ID を返さない

```bash
python -c "
import spidev
s = spidev.SpiDev(); s.open(3, 0); s.max_speed_hz = 1_000_000
print([hex(b) for b in s.xfer2([0x9F, 0, 0, 0])])
# 期待 (Pre-MVP W25Q64): [0x00, 0xEF, 0x40, 0x17]
# W25Q128 の場合:        [0x00, 0xEF, 0x40, 0x18]
# W25Q32 の場合:         [0x00, 0xEF, 0x40, 0x16]
"
```

- `[0x00, 0x00, 0x00, 0x00]` → 配線ミス、 CS/CLK 入れ替え疑い
- `[0x00, 0xFF, 0xFF, 0xFF]` → 3.3V が来ていない、 WP/HOLD のプルアップ確認
- そもそも spidev3.0 が無い → SPI 有効化 (Phase 3.2) からやり直し

### USB マイクが認識されない

```bash
arecord -l
# card 0: USB 等が出ていればOK
arecord -D plughw:0,0 -f cd -d 5 test.wav
aplay test.wav

python -c "import sounddevice; print(sounddevice.query_devices())"
```

### Bonsai 推論が極端に遅い (< 3 t/s)

- ZRAM が有効か `free -h` で確認
- `htop` で CPU フルロードしているか確認
- 量子化レベルを下げる: Q4_K_M → Q3_K_S (約 600MB)
- フォールバック: Bonsai → Qwen2.5-0.5B-Instruct (350MB, 15-25 t/s)

### サーマルスロットリング

```bash
watch -n1 'cat /sys/class/thermal/thermal_zone0/temp'
# 70℃ 以上が続くなら冷却強化:
#   - 銅製ヒートシンクを大型のものに
#   - 5V GPIO から小型ファン給電 (40mm, 30CFM 以下)
```

### systemd サービスが起動直後に落ちる

```bash
journalctl -u ghost-printer.service -n 100 --no-pager
# 典型原因:
#   - .venv のパスが /home/pi/... のままになっている → ExecStart を /home/haru に
#   - portaudio が未準備 → After=sound.target を Unit に追加
#   - SPI 権限 → SupplementaryGroups=spi を Service に追加、 haru が spi グループに居るか groups コマンドで確認
```

---

## ディレクトリ構成 (デプロイ後)

```
/home/haru/ghost-printer/
├── .venv/
├── main.py / mic_capture.py / flash_cortex.py
├── soul_binary.py / soul_log.py / soul_storage.py     # A6 layer
├── soul_schema.py / soul_engine.py / extractor.py
├── permission_gateway.py / capability_token.py
├── cortex_manager.py / soul_cortex.py / watchpoint.py
├── requirements.txt
├── ghost-printer.service
├── ghost-printer-evolve.service / .timer
├── whisper.cpp/ (built)
├── llama.cpp/ (built)
├── models/
│   ├── ggml-tiny.bin
│   ├── bonsai-1.7b-q4.gguf
│   └── (MiniLM ONNX 等)
├── data/
│   ├── master.key      # 32 B (本来は安全な KMS に)
│   ├── CORTEX.bin
│   ├── soul.json       # legacy 互換
│   ├── soul.bin        # A6 暗号化スナップショット
│   └── soul.log        # A6 暗号化追記ログ
├── specs/
│   ├── a6_soul_binary_format.md
│   ├── b1_hardware_feasibility.md
│   ├── b2_opi3b_migration.md
│   └── c1_disclosure_spec.md
└── logs/
```

---

**メンテナンス後の合言葉:**

```bash
systemctl status ghost-printer && python main.py --status
```

困ったら `ROADMAP.md §5 成功基準` と `specs/b2_opi3b_migration.md §9 リスク表` を見返す。
