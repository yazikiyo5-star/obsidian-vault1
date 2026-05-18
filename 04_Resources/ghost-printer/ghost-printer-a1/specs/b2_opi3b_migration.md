# B2 — Orange Pi 3B 移行仕様書

**更新日:** 2026-05-04
**ステータス:** Approved (qvp 採用済み, 実機 OPi 3B 4GB 入手済み)
**前提:** B1 の Pi 5 前提パイプラインを実機 OPi 3B に移植する
**関連:** `specs/b1_hardware_feasibility.md` (Pi 5 時代), `SETUP_OPI3B.md` (実機手順)

---

## 0. なぜ Pi 5 から OPi 3B に切り替えたか

- **価格**: OPi 3B 4GB ≈ ¥6,000 vs Pi 5 8GB ≈ ¥14,000。 Ghost-Printer のような分散デバイスは台数を増やしやすい価格帯が望ましい
- **電力**: OPi 3B の TDP ≈ 3-5W (アイドル 1W) vs Pi 5 の TDP 12W (アイドル 3W)。 バッテリ駆動の現実性が大きく違う
- **可用性**: Pi 5 の品薄が時々起きる。OPi 3B は SHENZHEN 生産で安定
- **十分な性能**: 後述の通り、 Bonsai 1.7B Q4 量子化モデルが 5-10 t/s で動く。 ambient capture には十分
- **GPIO 互換**: 40-pin ヘッダで Pi と同じレイアウト。既存 Pi 用 HAT がほぼ流用可能
- **eMMC ソケット**: OPi 3B は基板裏に eMMC モジュールスロットがある。 SD 摩耗対策として eMMC への移行が容易

---

## 1. ハードウェアスペック比較

| 項目 | Pi 5 (8GB) | **OPi 3B (4GB)** | コメント |
|------|-----------|------------------|---------|
| SoC | BCM2712 | Rockchip RK3566 | |
| CPU | 4× Cortex-A76 @ 2.4 GHz | 4× Cortex-A55 @ 1.6-1.8 GHz | A55 は A76 比 ~30-50% 性能 |
| GPU | VideoCore VII | Mali-G52 (NEON SIMD) | 推論には未使用 |
| RAM | 8 GB LPDDR4X | 4 GB LPDDR4 | OPi 3B でも Bonsai 1.7B 余裕 |
| ストレージ | microSD のみ | microSD + **eMMC ソケット** | eMMC 移行で SD 摩耗回避 |
| USB | 2× USB 3.0 + 2× USB 2.0 | 1× USB 3.0 + 2× USB 2.0 | マイクは USB 2.0 推奨 (Pi 同様) |
| 有線NW | GbE | GbE | 同等 |
| 無線 | WiFi 5 + BT 5.0 | WiFi 5 + BT 5.0 | 同等 |
| GPIO | 40-pin | 40-pin (Pi 互換配列) | 既存 HAT 流用可 |
| HDMI | 2× micro-HDMI | 1× HDMI 2.0a | OPi 3B のほうが配線簡単 |
| 電源 | USB-C 5V/5A 必須 | USB-C 5V/2-3A | OPi 3B は普通の 18W 充電器でOK |
| 冷却 | アクティブクーラー強推奨 | ヒートシンクのみで実用域 | ファン不要 |
| 寸法 | 85×56 mm | 85×56 mm | ほぼ同寸 |
| 価格目安 | ¥14,000 (8GB) | ¥6,000 (4GB) | |

---

## 2. CPU 性能比較とモデル動作見積もり

### 2.1 ベンチマーク予測 (相対性能)

| 演算 | Pi 5 (A76 @ 2.4G) | OPi 3B (A55 @ 1.8G) | 比率 |
|------|------------------|---------------------|------|
| 整数 SIMD | 100% | 35-45% | A55 は順序実行コアで A76 比 ~40% |
| 浮動小数 (NEON) | 100% | 40-50% | |
| メモリ帯域 | 17 GB/s (LPDDR4X) | 10-12 GB/s (LPDDR4) | LLM 推論に効く |
| シングルコア | 100% | ~35% | |
| マルチコア (4 core) | 100% | ~40% | |

### 2.2 モデル別予測動作速度

| モデル | サイズ | Pi 5 実測 | **OPi 3B 予測 (旧)** | **OPi 3B 実測 (2026-05-06)** | 用途上の許容 |
|--------|-------|----------|---------------------|-----------------------------|---------------|
| Whisper tiny (Q5) | ~75 MB | 0.6 s / 10s音声 | 1.5-2.5 s / 10s音声 | **6.9 s / 10s音声** (t=4)、 1.45× realtime | ambient mode 限定、 reactive 不可 |
| Bonsai 1.7B (Q1_0)* | **248 MB** | (未計測) | 5-10 t/s (Q4_K_M 想定) | **pp 4.2 t/s / tg 3.03 t/s** (t=4) | 1 抽出 100 token で 30-40 秒、 ambient OK / chat NG |
| Qwen2.5-0.5B (Q4) | ~350 MB | 80+ t/s | 15-25 t/s | (未計測、 fallback として温存) | 即時応答が必要な場合 |
| MiniLM-L6-v2 (ONNX) | ~90 MB | <100 ms/文 | 200-400 ms/文 | (未計測) | embedding キャッシュで実質ゼロ |

> *Bonsai は当初 Q4_K_M ~1GB を採択していたが、 prism-ml/Bonsai-1.7B-gguf の実体は **Qwen3 1.7B を Q1_0 に 1-bit 量子化したもの (248 MB)**。 PROGRESS_REPORT.md と整合 (240MB)。

> **2026-05-06 OPi 3B 実機ベンチ結果** (Trixie 26.2 / kernel 6.18.26 / Bonsai 1.7B Q1_0 / llama.cpp build d5003b6):
>
> - Bonsai thread sweep (4 thread が最良): pp32 = `2.49→3.17→4.20 t/s`、 tg64 = `1.77→2.30→3.03 t/s` for `t=2,3,4`
> - **生成 3 t/s は予測 5-10 t/s の 30-60%**: 原因は Q1_0 dequant kernel の ARM NEON A55 最適化が CUDA/Metal に追いついていないこと + thermal (下記)
> - Whisper tiny JFK 11秒: t=4 で `7.57s real` (1.45× realtime)。 予測 1.5-2.5s より約 3× 遅い
> - **熱**: ベンチ中 **72.2℃** に到達 (spec 目標 <70℃ 超え)。 thermal throttling で性能下振れの可能性あり → **小型ファン (5V 30mm) を GPIO 給電で追加することを強く推奨** (spec b2 §9 リスク表に既載)

### 2.3 1 入力ループのタイムバジェット (OPi 3B 実測ベース)

```
[音声録音 10s] → [Whisper tiny 6.9s] → [Bonsai 抽出 ~33s for 100 token]
              → [MiniLM 埋込 ~0.3s] → [SOUL 更新 ~0.05s]
              → 完了 (~50秒)
```

> **判断 (2026-05-06 更新):** ambient capture モードでは 1 入力 50 秒、 1 時間あたり **40-70 入力** 程度が現実的。 これは Pre-MVP の ambient 想定 (sleep 4-6 時間で 100-300 抽出) を満たす。 reactive な対話には Bonsai は遅すぎるため、 reactive 系は **Qwen2.5-0.5B Q4 fallback** または外部 AI (Claude API) で対応。 なお ファン追加 + thread tuning で 1.5-2× の改善余地あり (Phase 9 で再計測)。

---

## 3. OS イメージの選定

### 3.1 候補

| OS | カーネル | パッケージ | 推奨度 | 備考 |
|----|---------|------------|---------|------|
| **Armbian Bookworm Minimal** | mainline 6.x | Debian 12 | ★★★★★ | 一番安定。 SPI/I2C を `armbian-config` で簡単に有効化 |
| Joshua Riek Ubuntu Rockchip | mainline 6.x | Ubuntu 22.04/24.04 | ★★★★ | デスクトップが要るなら |
| Orange Pi 公式 OS | vendor 5.x BSP | Debian 11 / Ubuntu 22.04 | ★★ | 古めの BSP カーネル、 まれに不具合 |
| DietPi | カスタム | Debian | ★★★ | サーバ寄り、 軽量化に強い |

### 3.2 採択: **Armbian Bookworm Minimal (server, no desktop)**

理由:
- mainline kernel 6.x で RK3566 サポートが安定
- `armbian-config` (TUI) で SPI / I2C / serial 切替が即時
- Debian 12 base で apt エコシステムがそのまま使える (Bookworm)
- minimal/server image なので 2 GB 程度で OS が収まる
- `python3.11` が apt で入る → venv 構築が容易

ダウンロード元: <https://www.armbian.com/orangepi-3b/> (server 版を選択)

> **2026-05-05 注釈** (qvp 承認、 外部要因による追従):
>
> Phase 1 実機セットアップ時、 Armbian community が OPi 3B 向け **Bookworm を archive 済み** (community/releases per_page=30 走査範囲内に Bookworm Minimal の release asset 不在、 最新 30 release はすべて Trixie current のみ提供)。 採択精神 = **「Armbian の minimal イメージを使う」** を維持し、 Debian release のみ Trixie へ追従する。
>
> - **採用 release**: Armbian community **26.2.0-trunk.843** / Orangepi3b / **trixie** / current **6.18.26** / minimal
> - 中身: Debian 13 (Trixie) / mainline kernel 6.18.26 / Python 3.12+
> - SHA256 (DL 直後、 母艦 Mac で計測): `339780e04c4ef7a38846f248de8eea9800467530aaacf1f6a15d9bfbecc496d3`
> - 入手 URL: <https://github.com/armbian/community/releases/download/26.2.0-trunk.843/Armbian_community_26.2.0-trunk.843_Orangepi3b_trixie_current_6.18.26_minimal.img.xz>
>
> **影響**:
> 1. §2.2 のベンチ予測値は Bookworm + Pi 5 比較ベースなので Phase 9 で **再計測必須** (kernel 6.18 で rk3566 サポート改善の可能性あり、 Bonsai 1.7B Q4 が予測 5-10 t/s より上振れ/下振れする可能性両方ある)
> 2. **Python 3.12 / aarch64** で Pre-MVP 依存 wheel が揃わないリスク (特に `webrtcvad`、 `spidev`) は §9 リスク表参照
> 3. SPI overlay 名が kernel 6.18 で変わっている可能性 (§9 リスク行 既載) — Phase 3 で `armbian-config` または `/boot/dtb/rockchip/overlay/` から該当名を再特定する
> 4. 採択行 (本セクション 1 行目 "Armbian Bookworm Minimal") は **意図的に変更しない**: 採択履歴の維持と外部要因追従の経緯を分けて記録するため
>
> 確認した代替経路 (どれも当時不採用): Armbian 公式 dl.armbian.com (404)、 imola.armbian.com archive/ (Bookworm 不在)、 mirrors.dotsrc.org (404)、 OPi 公式 OS (BSP kernel で SETUP_OPI3B.md 大幅書換が必要)、 古い release を 100+ 件遡って Bookworm を探す (EOL 近い OS を採用することになる)

> **2026-05-05 実機検証結果** (Phase 1-3 完走、 qvp 承認):
>
> | 項目 | 旧採択 (Bookworm 想定) | Trixie + kernel 6.18 で実測 | 対応 |
> |------|----------------------|--------------------------|------|
> | SPI3 overlay 名 | `overlays=spi-spidev` | 実ファイル `/boot/dtb/rockchip/overlay/rockchip-rk3566-spi3-m0-cs0-spidev.dtbo` のみ存在 | `overlay_prefix=rockchip-rk3566` + `overlays=spi3-m0-cs0-spidev` の 2 行で `<prefix>-<overlays>.dtbo` として参照させる |
> | armbianEnv.txt の overlay_prefix | (デフォルト未設定) | `overlay_prefix=rk35xx` で配布 (実ファイル名と不整合) | sed で `rockchip-rk3566` に書換 |
> | ZRAM swap | `apt install zram-tools` + `systemctl enable zramswap` | Armbian 組み込みの `armbian-zram-config` が起動時に /dev/zram0 (≈2GB, 50% RAM) を **既に確保** している | `zram-tools` は不要。 誤って入れた場合は `systemctl mask zramswap.service` で衝突回避 |
> | spi / gpio / i2c グループ | (Bookworm では存在前提) | Trixie ベースイメージは **作成しない** | `groupadd -f spi gpio i2c` で先に作る + udev ルール `90-ghost-printer-perms.rules` で /dev/spidev* /dev/gpiochip* /dev/i2c-* にグループ所有を割当 |
> | デバイス検証 | `crw-rw---- 1 root spi /dev/spidev3.0` | 実機で確認済み (2026-05-05 23:39 JST) | OK |
> | thermal | <70℃ 維持 | 起動 3h で 45-55℃ | OK |
>
> 上記により Phase 3 の手順 (`SETUP_OPI3B.md §3.2-§3.4`) は **書換済み**。 採択精神 = "SPI3 を /dev/spidev3.0 として haru から読める形で出す + Bonsai 用に十分な swap" は完全維持。

### 3.3 焼き方

```bash
# 母艦 mac で:
brew install --cask balenaetcher  # または rpi-imager
xz -d Armbian_*_Orangepi3b_bookworm_*.img.xz
# Etcher で .img を microSD に焼く

# 初回起動: HDMI モニタ + USB キーボード を繋ぐ
# root password を作成 → user 'haru' を作成
# IPアドレスをメモ → 母艦から SSH に切替
```

---

## 4. GPIO / SPI 配線 (W25Q シリーズ Flash 接続)

### 4.1 OPi 3B の SPI 系統

OPi 3B には複数の SPI インターフェースがあるが、 mainline kernel + Armbian 構成では **SPI3 (M0)** が 40-pin ヘッダに出ている。

#### 配線表 (W25Q シリーズ DIP-8 ↔ OPi 3B)

> **対象チップ:** Winbond W25Q シリーズの DIP-8 パッケージ全般 (W25Q64 / W25Q128 等)。 ピン配置と SPI コマンドセットが共通なので同じ配線で動作する。 Pre-MVP は **W25Q64FVAIG (aitendo 19957)** を採択 (§10 注釈参照)。

| DIP-8 ピン | 信号       | OPi 3B 物理ピン | OPi 3B 機能名      | 備考 |
|------------|-----------|-----------------|--------------------|------|
| 8 VCC      | 3.3V      | 1 or 17         | 3V3                | 5V は絶対NG |
| 4 GND      | GND       | 6, 9, 14, 20…   | GND                | 適当な GND |
| 1 /CS      | SPI3_CS0  | **24**          | SPI3_CS0_M0        | |
| 2 DO (MISO) | SPI3_MISO | **21**         | SPI3_MISO_M0       | |
| 3 /WP      | (プルアップ) | 1 or 17       | 3V3                | 必ず 3V3 へ |
| 6 CLK      | SPI3_CLK  | **23**          | SPI3_CLK_M0        | |
| 5 DI (MOSI) | SPI3_MOSI | **19**         | SPI3_MOSI_M0       | |
| 7 /HOLD    | (プルアップ) | 1 or 17       | 3V3                | 必ず 3V3 へ |

**JEDEC ID で動作確認:** `flash_cortex.py --read-id --simulate` (まずシミュレータ)、 実機で `--bus 3` を付けて実行。
- W25Q64:  `0xEF 0x40 0x17` (8MB) ← Pre-MVP 採択
- W25Q128: `0xEF 0x40 0x18` (16MB)
- W25Q32:  `0xEF 0x40 0x16` (4MB)

**注意:** Pi 5 の場合は SPI0 (CE0) が pin 24 で /dev/spidev0.0 として出ていたが、 OPi 3B では SPI3 が出るので **`/dev/spidev3.0`** になる (詳細は §4.3)。

### 4.2 SPI 有効化

**Trixie + kernel 6.18 で検証済みの手順 (2026-05-05、 推奨):**

```bash
# /boot/armbianEnv.txt を直編集
sudo sed -i 's/^overlay_prefix=.*/overlay_prefix=rockchip-rk3566/' /boot/armbianEnv.txt
echo 'overlays=spi3-m0-cs0-spidev'   | sudo tee -a /boot/armbianEnv.txt
echo 'param_spidev_spi_bus=3'         | sudo tee -a /boot/armbianEnv.txt

sudo reboot

# 再起動後:
ls -la /dev/spidev*
# 期待: crw-rw---- 1 root spi 153, 0 ... /dev/spidev3.0
```

**オーバーレイ名は Armbian + kernel バージョンで揺れる** ので、 動かないときは現物確認:

```bash
ls /boot/dtb/rockchip/overlay/ | grep -i spi
# kernel 6.18 / Trixie では:
#   rockchip-rk3566-spi3-m0-cs0-spidev.dtbo  ← OPi 3B 用
#   rockchip-rk3568-hk-spi-spidev.dtbo
#   rockchip-rk3399-spi-spidev.dtbo
#   その他
# overlay_prefix= と overlays= の組み合わせで <prefix>-<overlays>.dtbo になるよう設定する
```

旧採択 `overlays=spi-spidev` は kernel 5.x / Bookworm 時代の名前。 kernel 6.18 では存在しない (採択精神 = "SPI3 を /dev/spidev3.0 として出す" は維持)。

### 4.3 `flash_cortex.py` のデバイスパス変更

現行コードは `spidev.SpiDev().open(0, 0)` で `/dev/spidev0.0` を開きに行く。 OPi 3B では bus が変わるため:

```python
# soul/flash_cortex.py の SpiDev 初期化箇所 (例):
SPI_BUS = 3       # OPi 3B: spidev3.0 / Pi 5: spidev0.0
SPI_DEVICE = 0
spi = spidev.SpiDev()
spi.open(SPI_BUS, SPI_DEVICE)
```

`flash_cortex.py` に環境変数 (例: `GPP_SPI_BUS=3`) または引数で切替可能にする実装を **`B3` で追加予定**。

### 4.4 USB マイク

OPi 3B も Pi と同じく USB 2.0 ポートにつなぐ。 ALSA は `arecord -l` で見える。 `sounddevice` も問題なく動作 (libportaudio2 + portaudio19-dev が必要)。

---

## 5. 電源とバッテリ駆動

| 構成 | 必要電流 | 備考 |
|------|---------|------|
| アイドル | 0.4 A | 1.0 W |
| Bonsai 推論中 | 1.0-1.4 A | 4-5 W、 4 コアフルロード |
| Whisper 推論中 | 0.6-0.8 A | 2-3 W |
| ピーク (起動時など) | 1.8 A | 9 W |

**推奨電源:** USB-C 5V/3A (15W) のもの。 普通のスマホ用 18W PD 充電器でも可。 PD でなくても OK。

**バッテリ駆動の現実:** 5000 mAh モバイルバッテリ (5V/2.4A 出力) で連続稼働 12-15 時間。 Pi 5 だと同じバッテリで 5-7 時間しか持たないので、 OPi 3B のメリットが大きい。

将来的には PiSugar 3 のような HAT が OPi 3B にも乗る (ピン互換)。

---

## 6. eMMC への OS 移行 (Phase 7 として将来追加)

OPi 3B 基板裏には eMMC モジュールスロットがある。 32GB の eMMC モジュール (¥1,500-2,000) を装着すると:

- ランダム IO が SD の 5-10 倍 → systemd ブートが体感速い
- 書込み寿命が SD の 100 倍以上 → 24 時間稼働でも長持ち
- microSD はバックアップ/レスキュー用に温存

移行手順 (将来):

```bash
# eMMC 装着後、 SD ブート状態で:
sudo armbian-install
# → 'Boot from eMMC, install to eMMC' を選択
# → SD イメージを丸ごと eMMC にコピー
# → SD を抜いて再起動
```

**MVP 段階では microSD で十分**。 24 時間連続稼働の安定性が確認できてから eMMC へ。

---

## 7. ソフトウェア側で必要な変更

| 場所 | Pi 5 前提 | OPi 3B 対応 |
|------|----------|--------------|
| `flash_cortex.py` | `spidev0.0` 決め打ち | `--bus 3` 引数 / 環境変数で切替 |
| `requirements.txt` | `spidev>=3.6; aarch64` | OK そのまま |
| `mic_capture.py` | ALSA カード ID 0/1 想定 | `--device` で柔軟に。 OPi 3B は通常 0 |
| `cortex_manager.py` | hardware 不問 | 変更不要 |
| `main.py` | hardware 不問 | 変更不要 |
| `ghost-printer.service` | `User=pi` | `User=haru` (OPi のデフォルトユーザに合わせる) |
| `SETUP.md` | `raspi-config` | `armbian-config` に置換 → `SETUP_OPI3B.md` 新設 |

`flash_cortex.py` への変更は B3 タスクで実施 (本仕様完成後)。

---

## 8. 性能ベンチの実測手順 (実機到着後)

実 OPi 3B で動作確認後、以下を計測してこの仕様書 §2.2 を実値に置換する:

```bash
# A. CPU シングルコア性能
sysbench cpu run --threads=1
sysbench cpu run --threads=4

# B. メモリ帯域
sysbench memory run --memory-block-size=1M --memory-total-size=10G

# C. Whisper tiny で 10s 日本語音声
time ./whisper.cpp/main -m ggml-tiny.bin -l ja -f sample_10s.wav

# D. Bonsai 1.7B で 100 token 出力
time ./llama.cpp/main -m bonsai-1.7b-q4.gguf -p "test" -n 100

# E. MiniLM-L6-v2 で 100 文埋込
python -c "from sentence_transformers import SentenceTransformer; \
  import time; m = SentenceTransformer('all-MiniLM-L6-v2'); \
  t0 = time.time(); m.encode(['テスト'] * 100); print(time.time() - t0)"
```

結果を `specs/b2_opi3b_migration.md §2.2` に追記して、 PROGRESS_REPORT を更新する。

---

## 9. リスクと対策 (OPi 3B 固有)

| リスク | 影響 | 対策 |
|-------|------|------|
| Bonsai 推論が想定より遅い (5 t/s 未満) | UX 悪化 | Q4_K_M → Q3_K_S にダウン量子化 / Bonsai → Qwen2.5-0.5B フォールバック |
| Armbian の SPI オーバーレイ名が版違いで通らない | flash_cortex 動かず | `/boot/armbianEnv.txt` を直編集、 dts オーバーレイ自前ビルド |
| WiFi 5 が屋内で不安定 | デバイス分散同期に支障 | 有線 GbE 使用 / BLE への切替 (Soul Protocol L2) |
| サーマルスロットリング | 長時間推論で性能低下 | ヒートシンク + 小型ファン (5V) を GPIO から給電 |
| eMMC 互換性 | 動作しないモジュールあり | 公式推奨 (Foresee 32GB) を使う |
| RK3566 BSP の Bluetooth が不安定 | COLMI R02 受信に影響 | mainline + bluez 安定版を使う / USB BT ドングル併用 |
| **Bookworm 撤回 → Trixie 移行** (2026-05-05 発覚) | Pre-MVP の Python 依存 wheel が python 3.12 / aarch64 で揃わない可能性 (`webrtcvad`、 `spidev`、 `numpy`、 `sounddevice` 等) | Phase 4 自己テスト 98 件で early detection、 ダメなら apt 系 (`python3-webrtcvad`、 `python3-spidev`) で fallback、 それでもダメなら `pyenv` で python 3.11 を別途構築 |
| ✅ **kernel 6.18 で SPI3 overlay 名変更** (Trixie 26.2 採用) — *2026-05-05 解消* | `/dev/spidev3.0` が出ず flash_cortex 不能 | **解消済み**: `overlay_prefix=rockchip-rk3566` + `overlays=spi3-m0-cs0-spidev` で `/dev/spidev3.0` 出現確認 (haru:spi 0660)。 詳細は §3.2 footnote / §4.2 |
| ✅ **ヘッドレス Wi-Fi setup の挙動が版違い** — *2026-05-05 N/A* | Phase 2 で SSH 開通できず詰む | **回避**: HDMI モニタ + USB キーボードでの初回 TUI ログイン経路を採用したため発火せず (`SETUP_OPI3B.md §1.3.1` は「将来のテザリング構成」用に温存) |
| ✅ **Trixie で spi/gpio/i2c グループが未作成** — *2026-05-05 解消* | `usermod -aG spi haru` が "グループが存在しません" で失敗 + `/dev/spidev3.0` の所有グループ未定義 | **解消済み**: `groupadd -f spi gpio i2c` + udev ルール `90-ghost-printer-perms.rules` (`KERNEL=="spidev[0-9]*.[0-9]*", GROUP="spi", MODE="0660"`) を導入。 詳細は `SETUP_OPI3B.md §3.4` |
| ✅ **Trixie image は armbian-zram-config 組み込みで /dev/zram0 を確保済み** — *2026-05-05 解消* | `zram-tools` の `zramswap.service` が同じデバイスを取り合って "Device or resource busy" で起動失敗 | **解消済み**: `zram-tools` は使わず Armbian 組み込みの 2GB zram に任せる (誤って入れたら `systemctl mask zramswap.service`)。 詳細は `SETUP_OPI3B.md §3.3` |

---

## 10. ロードマップ更新差分

`ROADMAP.md` §2 の購入リストを以下に置換:

| # | 品目 | 型番 | 個数 | 概算 |
|---|------|------|------|------|
| 1 | Orange Pi 3B (4GB) | 本体 ✅ 入手済み | 1 | ¥6,000 |
| 2 | USB-C 電源 (5V/3A 18W) | 任意の PD 充電器 | 1 | ¥1,500 |
| 3 | ヒートシンク (大型, 銅) | OPi 3B 用 | 1 | ¥800 |
| 4 | microSD 64GB (A2) | SanDisk Extreme | 1 | ¥2,500 |
| 5 | USBコンデンサーマイク | 小型・単指向 | 1 | ¥3,000 |
| 6 | W25Q64 SPI Flash (DIP-8) | aitendo 19957 (W25Q64FVAIG) | 1 | ¥209 |
| 7 | ジャンパ線 + ブレッドボード | セット | 1 | ¥1,500 |
| 8 | OPi 3B 用ケース (GPIO 開口あり) | 公式 / Aliexpress 互換品 | 1 | ¥2,000 |
| 9 | (任意) eMMC モジュール 32GB | Foresee 公式互換 | 0-1 | ¥1,800 |
| | **合計 (microSD で MVP まで)** | | | **約 ¥17,509** |

Pi 5 構成 (¥30,200) 比で **約 ¥12,700 の節約**。

> **2026-05-17 注釈 (qvp 承認):** #6 は当初 Adafruit 3664 系 W25Q128 (16MB, ¥500) を採択していたが、 Switch Science 8591 / 共立 ADA-5634 / Adafruit 5643 が全部売り切れ。 一方 `data/CORTEX.bin` 実サイズは **2,759 バイト (2.7KB)** で 8MB あれば 3000 倍の余裕があるため、 aitendo W25Q64FVAIG DIP-8 (¥209、 在庫 204点 @ 2026-05-17) を採択。 W25Q シリーズはピン配置とコマンドセットが共通なので flash_cortex.py の動作はそのまま (Pre-MVP 用に `EXPECTED_CAPACITY_ID = 0x17` / `CHIP_CAPACITY = 8MB` に変更済み)。 W25Q128 への将来回帰オプションは温存する (`EXPECTED_CAPACITY_ID = 0x18` / `CHIP_CAPACITY = 16MB` に戻すだけ)。 詳細は memory `project_ghost_printer_flash_chip_swap.md` 参照。

---

## 11. 次のステップ

1. ✅ **B3 — `flash_cortex.py` を OPi 3B 対応化** — 2026-05-04 完成
   - `--bus N` `--device N` `--speed-hz N` の CLI 引数追加
   - 環境変数 `GPP_SPI_BUS` `GPP_SPI_DEVICE` `GPP_SPI_SPEED_HZ` でデフォルト指定可能
   - `--read-id` で JEDEC ID + 容量解釈表示 (配線確認の最初のステップ)
   - ヘルプにも Pi 5 (bus 0) / OPi 3B (bus 3) の例を埋込
   - 11/11 テスト全通過 (argparse / env / CLI round-trip / read-id)
2. ✅ **`SETUP_OPI3B.md` 新設** — Phase 0-9 完備
3. **実機到着後**: §8 のベンチ実施、 §2.2 を実測値に置換、 リスク表を更新
4. **将来 (B4)**: COLMI R02 BLE 受信プロト、 Soul Dock のピン互換確認

---

*この仕様書は Pi 5 → OPi 3B 移行の判断材料と作業差分を一元化するためのもの。 実機ベンチが取れたら §2.2 を実測に書き換えて確定版にする。*
