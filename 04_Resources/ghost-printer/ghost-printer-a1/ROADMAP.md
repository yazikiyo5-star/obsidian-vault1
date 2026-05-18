# Ghost-Printer ロードマップ — 現在地から実機稼働まで

**更新日:** 2026-05-04 (OPi 3B 移行を反映)
**担当:** qvp (haru)
**前提:** Pre-MVP、ソフトウェア側は Track A/A6/C 全完了 (合計 232 件全テストパス)。
HW は **Orange Pi 3B (RK3566, 4GB)** を採用 (旧 Pi 5 案からの移行理由は `specs/b2_opi3b_migration.md`)

---

## 0. 現在の到達点

| 領域 | 状態 |
|------|------|
| SOUL抽出パイプライン (Track A) | ✅ 実装・検証済み |
| Permission Gateway / Capability Token (Track C) | ✅ 実装・検証済み |
| Soul Cortex（3モデル協調） | ✅ 実装・25+7テストパス |
| CORTEX Manager（バイナリ永続化） | ✅ 実装・42テストパス |
| Watch Point（観測生態系） | ✅ 実装・38テストパス |
| ハードウェア実機 | ❌ 未着手（本ロードマップの主題） |

---

## 1. 届くまで（〜ハード到着）にやること

実機が届いてから手探りで詰まらないように、"組み立てたら動く"状態で渡せるよう先回りでコードを用意する。

### 1.1 実装待ちスクリプト

| 項目 | 目的 | 備考 |
|------|------|------|
| `flash_cortex.py` | W25Q シリーズ SPI Flash (Pre-MVP は W25Q64) へ `CORTEX.bin` を焼く | 消去→書込→ベリファイ。Pi到着前にシミュレータでテスト |
| `mic_capture.py` | USBマイクから音声取得＋VAD＋`whisper.cpp`呼び出し | PortAudio / webrtcvad / subprocess パイプ |
| `main.py` 拡張 | `--mic` `--flash-cortex` `--status` `--evolve` 追加 | 既存の `--input` `--interactive` と共存 |
| `ghost-printer.service` | systemdユニット（常駐化） | マイク監視 → 自動処理 → 定期 evolve |
| `requirements.txt` 最終化 | 依存性棚卸し | numpy / sounddevice / webrtcvad / spidev / httpx |
| `SETUP.md` | 実機上でコピペするだけの手順書 | 6フェーズ全ての具体コマンド |

### 1.2 実装方針

- **スタブ優先** — 実機がなくても動作確認できるようモック（ダミーSPI、ダミー音声）を併走実装
- **テスト先行** — `test_flash_cortex.py` でシミュレータモードを検証してから実機で流す
- **段階的配線** — 一気通貫で動かそうとせず、各段階を独立してテスト可能にしておく

---

## 2. 購入リスト (確定版・OPi 3B MVP)

実機 HW を Pi 5 から **Orange Pi 3B** に変更。 約 ¥12,700 の節約。
詳細は `specs/b2_opi3b_migration.md §10`。

| # | 品目 | 型番 | 個数 | 概算 |
|---|------|------|------|------|
| 1 | Orange Pi 3B (4GB) | 本体 ✅ **入手済み** | 1 | ¥6,000 |
| 2 | USB-C 電源 (5V/3A 18W) | 任意の PD 充電器 | 1 | ¥1,500 |
| 3 | ヒートシンク (大型, 銅) | OPi 3B 用 | 1 | ¥800 |
| 4 | microSD 64GB (A2) | SanDisk Extreme | 1 | ¥2,500 |
| 5 | USBコンデンサーマイク | 小型・単指向 | 1 | ¥3,000 |
| 6 | W25Q64 SPI Flash (DIP-8) | aitendo 19957 (W25Q64FVAIG) | 1 | ¥209 |
| 7 | ジャンパ線 + ブレッドボード | セット | 1 | ¥1,500 |
| 8 | OPi 3B 用ケース | 公式 / Aliexpress 互換品 | 1 | ¥2,000 |
| 9 | (任意) eMMC モジュール 32GB | Foresee 公式互換 | 0-1 | ¥1,800 |
| | **合計 (microSD で MVP まで)** | | | **約¥17,509** |

> **2026-05-17 #6 変更:** 当初 Adafruit 3664 系 W25Q128 (¥500) を予定していたが国内全滅。 CORTEX.bin 実サイズ 2.7KB に対し 8MB で十分なため、 aitendo W25Q64FVAIG DIP-8 (¥209) を採択。 詳細は `specs/b2_opi3b_migration.md §10` 注釈。

旧 Pi 5 構成 (¥30,200) は `SETUP.md` 末尾を参照。

---

## 3. 到着後のフェーズ（実働3〜5日）

```
Day1  │ Phase 1: HW組立 (30分) ─→ Phase 2: OS初期設定 (30分) ─→ Phase 3a: 依存導入 (半日)
Day2  │ Phase 3b: モデルDL ─→ Phase 4: 単体動作確認（whisper / llama / minilm をそれぞれ実行）
Day3  │ Phase 5: CORTEX.bin書き込み ─→ Phase 6a: パイプライン疎通
Day4  │ Phase 6b: systemd常駐化 ─→ 1日間の観測放置
Day5  │ 観測結果レビュー → CORTEX.binチューニング → 再書き込み
```

### Phase 1: HW組み立て (OPi 3B)
- microSD へ **Armbian Bookworm Minimal** を書込み (SSH 事前設定)
  - **2026-05-05 注釈**: Armbian community が Bookworm を archive 済みのため、 採択精神を維持しつつ **Trixie current 26.2.0-trunk.843 (Debian 13 / kernel 6.18.26)** に追従。 経緯と影響は `specs/b2_opi3b_migration.md §3.2` 注釈と §9 リスク表 を参照
- W25Q64 (DIP-8) をブレッドボードで **SPI3 (M0)** に接続: 物理pin 19/21/23/24 (MOSI/MISO/SCLK/CS) + 3V3/GND
- USB マイク接続

### Phase 2: OS初期設定 (OPi 3B)
- SSH ログイン → `apt update && apt upgrade`
- `armbian-config` で SPI 有効化 (overlays=spi-spidev, param_spidev_spi_bus=3)
- ZRAM スワップ 2GB 設定 (Bonsai 推論のメモリ余裕)

### Phase 3: 開発環境
- ビルドツール / Python venv / ffmpeg / portaudio 導入
- whisper.cpp / llama.cpp を `make` でビルド
- ONNX Runtime (MiniLM用)

### Phase 4: モデル配置
- `ggml-tiny.bin` (~75MB, Whisper)
- `bonsai-1.7b-q4_k_m.gguf` (~1GB) または Qwen2.5-0.5B (350MB) フォールバック
- `all-MiniLM-L6-v2.onnx` (~90MB)
- すべて `~/ghost-printer/models/` に配置し、CORTEX の `model_file` パスと突合

### Phase 5: CORTEX.bin の物理書き込み
- 手元PC側で `python cortex_manager.py` → `data/CORTEX.bin`
- OPi へ転送 → `python flash_cortex.py data/CORTEX.bin --bus 3`
- `python flash_cortex.py --verify data/CORTEX.bin --bus 3` で SHA256 一致確認

### Phase 6: 統合動作確認
- `python main.py --load-cortex` で SPI Flash → CORTEX 読込確認
- `python main.py --mic --duration 60` で 60 秒マイク取込 → SOUL 更新 (A6 ShadowStorage 経由)
- `python main.py --status` で `total_episodes` 増加と `core_identity` 変動を確認
- `python main.py --evolve` で Watch Point 進化サイクル手動実行
- `sudo systemctl enable --now ghost-printer` で常駐化

> 詳細手順は `SETUP_OPI3B.md` を参照。

---

## 4. 実機後に残る"その先"の課題

MVPが動いたら次に解くべき技術課題。今は棚上げ。

| 領域 | 課題 |
|------|------|
| ハードウェア小型化 | Tier1 (65×30mm) / Tier2 (カスタムPCB) への移行計画 |
| 電源 | バッテリ駆動（Pi 5 は 5V/3A 必要、Powerboost＋Li-Ion？） |
| 筐体 | 3Dプリント筐体、マイクの位置、熱対策 |
| Core/Peripheral分離 | BLE同期プロトコル、CRDT採用選定（Automerge等） |
| UI | 音声フィードバック、LED、スマホコンパニオンアプリ |
| プライバシー強化 | マイク常時 vs 押下起動、音声生データ非保存の検証 |
| CORTEX進化 | オンライン学習？ユーザーがCORTEX.binを直接編集できるUI |

---

## 5. 成功基準（MVPのゴール）

実機で以下が24時間回れば MVP 成功とする。

- [ ] マイクで10分のモノローグを録音→SOULが更新される
- [ ] `soul.json` の `core_identity` のμが観測値方向に動いている
- [ ] Watch Point が少なくとも1件生まれ、1サイクル進化で淘汰または昇格する
- [ ] CORTEX.binをチューニング→再書込→再起動で挙動が変わる（パラメータ可変性の実証）
- [ ] Permission Gatewayを経由して外部AI（Claude）に語り掛け→ペルソナ反映された応答が返る
- [ ] 24時間のsystemd常駐稼働でクラッシュなし

---

## 6. リスクと対策

| リスク | 影響 | 対策 |
|--------|------|------|
| Pi 5のARM上でllama.cpp推論が遅い | UX悪化 | Q4量子化＋温度低め＋max_tokens制限、必要なら1.5Bを0.5Bにフォールバック |
| W25Q シリーズ Flash への書き込み失敗・ブリック | CORTEX焼けない | Pi到着前にシミュレータで `flash_cortex.py` を徹底検証 |
| USBマイクのゲイン/ノイズ | 音声抽出精度劣化 | `alsamixer` でゲイン調整、VAD閾値をCORTEXで調整可能に |
| 熱暴走 | システム不安定 | Argon NEO 5などアクティブクーラー、`vcgencmd measure_temp` 監視 |
| SDカード寿命 | soul.jsonの頻繁書込で摩耗 | `fsync` の頻度を絞る（10件ごと等）、ログはtmpfs |

---

*次の作業: 1.1節の実装待ちスクリプト6件を本セッションで全部作り切る。*
