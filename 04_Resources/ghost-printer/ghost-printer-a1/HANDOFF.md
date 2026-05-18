# Ghost-Printer 実機セットアップ — 引き継ぎ書

**作成日:** 2026-05-04
**次回セッション (2026-05-05) で参照する**

---

## 状況

- **Pre-MVP 設計はすべて完了** (テスト 343 件全通過)
- **HW**: Orange Pi 3B (Rockchip RK3566, 4GB) 既に入手済み
- **microSD**: 64GB A2 が **2026-05-05 到着予定**
- **COLMI R02 リング**: 未着 (届き次第別タスク)
- **母艦**: Mac (本リポジトリのクローンと CORTEX.bin 生成元)

---

## 今日のゴール

実機が **24 時間 systemd 常駐稼働** するまで持っていく。 具体的には
`SETUP_OPI3B.md` の Phase 1〜9 を順次実行 (合計実働 1 日想定):

| Phase | 所要 | やること |
|-------|------|---------|
| 1 | 30分 | microSD に Armbian Bookworm Minimal を焼く |
| 2 | 30分 | 初回ブート + SSH 開通 (haru ユーザ作成) |
| 3 | 30分 | SPI 有効化 (`overlays=spi-spidev`, `param_spidev_spi_bus=3`) + ZRAM |
| 4 | 1時間 | Python venv + 依存導入 + **自己テスト 98 件パス確認** (A6 系) |
| 5 | 1-2時間 | Whisper tiny + Bonsai 1.7B Q4 + MiniLM ダウンロード + ベンチ |
| 6 | 30分 | W25Q64 (DIP-8) 配線 + `flash_cortex.py --bus 3` で書込み |
| 7 | 30分 | 統合動作確認 (mic 60 秒 → SOUL 更新) |
| 8 | 15分 | systemd 常駐化 (bonsai.service + ghost-printer.service + probe.timer) |
| 9 | 24時間 | 観測放置 + ベンチ実測 |

---

## 完成済み Track 一覧

すべての主要設計と Pre-MVP 実装が完了しています。

### Track A6 — SOUL バイナリフォーマット
- `specs/a6_soul_binary_format.md` (Approved 2026-05-03)
- HEADER 128B + TOC + 暗号化セクション (AES-256-GCM) + APPEND_LOG + Merkle 部分証明
- ファイル: `soul_binary.py`, `soul_log.py`, `soul_storage.py`, `permission_gateway.py`
- テスト: 98 件 (24+25+16+12+21)

### Track A7 — 適応型 Watch Point + LLM 健康監視
- `specs/a7_adaptive_watchpoint.md` (Approved 2026-05-04)
- AdaptivePolicy (RULES_ONLY/LLM_ONLY/HYBRID) + LlmHealthTracker + ChainedLlmCall + LlmRestartManager
- ファイル: `watchpoint_llm.py`
- テスト: 60 件 (25+21+14)
- **未実装の Tier 2** (おかしな挙動の検知) は実機データ取得後に A8 として別仕様化予定

### Track B — ハードウェア移行 + 周辺機器
- `specs/b2_opi3b_migration.md` (Approved 2026-05-04)
- `specs/b4_colmi_r02_protocol.md` (Draft, リング到着後に検証)
- `SETUP_OPI3B.md` (Phase 0-9 完備、 systemd 自動再起動含む)
- ファイル: `flash_cortex.py` (--bus 引数追加), `colmi_r02_protocol.py`,
  `colmi_r02_client.py`, `hrv_calculator.py`
- テスト: 51 件 (11 + 40)

### Track C6 — Soul Protocol L1-L5
- `specs/c6_soul_protocol.md` (Approved 2026-05-04)
- 仕様のみ。 Pre-MVP では実装ゼロ。 MVP β (実機 2 台 + Soul Dock 試作) 後に L1-L4 実装着手

### 既存 Track A1-A5, B1, C1-C5
- `PROGRESS_REPORT.md` の旧セクション参照
- テスト: 134 件

**プロジェクト全体: 343 件全通過**

---

## 採択済みの設計判断 (変更しないこと)

### A6 (Q1-Q8)
Little-Endian / zstd level 10 / AES-256-GCM (HKDF section keys, 決定論IV, AAD バインド) /
デバイス自動生成 owner_id_hash + Recovery seed / 毎日02:00+64KB超でコンパクション /
raw_text を暗号化 EPISODIC セクションに 30 日 TTL / format_version major 5bit/minor 11bit / `.soul`

### A7
HEALTHY → DEGRADED (3連続失敗) → DEAD (24h成功なし) → 復帰 (1回成功) /
DEAD 時 LLM スキップ / probe で復帰 / ChainedLlmCall で primary→secondary 自動 fallback /
LlmRestartManager (DEAD 5分超で外部コマンド restart, 1h 上限 3 回, cooldown 10分)

### B2 (OPi 3B)
SPI3 (M0) を `/dev/spidev3.0` として使用 / Armbian Bookworm Minimal /
USB-C 5V/3A / ヒートシンクのみ (ファン任意) /
購入済み: ¥6,000 (本体) + 周辺で計 ¥17,800

> **2026-05-05 実機セットアップ時の注釈** (qvp 承認):
> Armbian community が OPi 3B 用 Bookworm を archive 済みのため、 採択精神 = "Armbian の minimal イメージ" を維持しつつ Debian release のみ **Trixie 26.2.0-trunk.843 (kernel 6.18.26)** に追従。
> 経緯と影響と検証手順は `specs/b2_opi3b_migration.md §3.2` 注釈・ §9 リスク表、 `SETUP_OPI3B.md §1.3.1` (ヘッドレス Wi-Fi setup) を参照。
> 採択行 (本セクション 2 行目) は意図的に変更しない (採択履歴の維持)。

### C6 (Soul Protocol Q1-Q6)
内部 DID + 表示 fediverse handle / **Soul Dock 有線のみ** (BLE/Wi-Fi/mDNS 全て scope 外) /
**Soul Dock 物理接続のみで信頼起点** (友達交換 scope 外) /
entry 単位 + 不整合時 snapshot, **充電中ドック上で発動** /
Op-based CRDT 自前実装 / **「他者から見た私」は弱い prior 取込み** (係数 0.1-0.2、 実装は MVP β 後)

---

## 次回 Phase 1 から始める前のチェック

- [ ] 母艦 Mac で本リポジトリが最新 (PROGRESS_REPORT で `343 件全通過` を確認)
- [ ] `python cortex_manager.py` で `data/CORTEX.bin` が再生成可能か確認
- [ ] microSD カードが手元にあり、 母艦の SD アダプタも準備済み
- [ ] OPi 3B 本体が手元にあり、 USB-C 電源 (3A 以上) 用意済み
- [ ] HDMI モニタ + USB キーボードが初回ブート用に使えるか
- [ ] W25Q64 DIP-8 (aitendo 19957) とブレッドボード + ジャンパ線が手元にある (Phase 6 で必要)
- [ ] USB マイク (任意の単指向で OK) が手元にある (Phase 7 で必要)

---

## 実機到着後のフェーズ (Phase 9 終わったら)

優先順:

1. **`specs/b2_opi3b_migration.md §2.2` を実測ベンチで書換**
   - 性能予測値 (5-10 t/s) が実値とどれだけずれるか確認
   - ずれが大きければ Q4_K_M → Q3_K_S にダウン量子化検討

2. **Bonsai 1.7B を `LlmWpProposer` に実配線**
   - 現状: `LlmWpProposer(llm_call=Callable)` の Callable に extractor 風ラッパを渡す
   - extractor.py の Ollama 呼出パターンを参考にする
   - 動作確認したら `data/llm_health.json` に状態永続化を追加 (現未実装)

3. **`probe_llm_health` の systemd timer 配線**
   - `SETUP_OPI3B.md §8.4` の `ghost-printer-probe.timer` を実装
   - `main.py --probe-llm` のサブコマンド追加が必要

4. **COLMI R02 リング到着後**: scan + パケットダンプで実プロトコル検証 →
   `specs/b4 §1.3` のコマンド ID を実値で書換

5. **24 時間以上の安定稼働後**: A8 (Tier 2 = おかしな挙動の検知) を実機データを基に設計

---

## 参考ファイル一覧

| ファイル | 役割 |
|----------|------|
| `SETUP_OPI3B.md` | **明日のメイン手順書 (Phase 0-9)** |
| `ROADMAP.md` | 全体ロードマップ + 購入リスト |
| `PROGRESS_REPORT.md` | 進捗詳細 + 全成果物リスト |
| `specs/a6_soul_binary_format.md` | SOUL バイナリ |
| `specs/a7_adaptive_watchpoint.md` | 適応型 WP + LLM 健康監視 |
| `specs/b2_opi3b_migration.md` | Pi 5 → OPi 3B 移行 |
| `specs/b4_colmi_r02_protocol.md` | COLMI R02 BLE |
| `specs/c6_soul_protocol.md` | Soul Protocol L1-L5 |

---

## 引き継ぎ完了時に何が達成されているべきか

Phase 9 終了時:
- [ ] `systemctl is-active ghost-printer.service` → `active`
- [ ] `python main.py --status` で SOUL が成長していることを確認
- [ ] 24 時間で Watch Point が少なくとも 1 件生まれている
- [ ] CORTEX.bin が SPI Flash に焼かれている (`flash_cortex.py --verify`)
- [ ] crash なしで 24 時間継続
- [ ] specs/b2 §2.2 のベンチ予測値を実測値で更新済み

---

*相棒、 明日の朝にこのファイルと SETUP_OPI3B.md を開いて、 Phase 1 から始めよう。*
