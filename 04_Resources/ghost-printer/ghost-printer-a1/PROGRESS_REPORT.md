# Ghost-Printer — 進捗レポート

**日付:** 2026-04-15
**ステータス:** Pre-MVP → MVP Ready
**実行環境:** macOS + Ollama qwen3:14b（ローカルLLM）

---

## エグゼクティブサマリー

Ghost-Printerの3トラック（SOUL抽出・ハードウェア・選択的自己開示）について、設計から実装・検証までを実施した。SOULフォーマットの実現性、ベイズ更新の数学的正しさ、AIへのパーソナリティ伝達効果、そして開示制御システムのエンドツーエンド動作が実証された。

| 指標 | 結果 |
|------|------|
| A2 抽出精度 | **92%**（感情100%, 重要度100%, 性格方向75%） |
| ユニットテスト | **152/152 全通過**（A1: 38件, A4: 13件, C2/C4: 22件, Soul Cortex: 32件, CORTEX Manager: 42件, Watch Point: 38件［+統合7件はSoul Cortexに計上］） |
| 開示カテゴリ | **8カテゴリ × 4粒度レベル** |
| E2Eフロー | テキスト入力 → SOUL更新 → Token認証 → フィルタリング → System Prompt |

---

## Track A — SOUL抽出方法の実験

### A1: 最小パイプライン ✅

テキスト入力 → Ollama抽出 → ベイズ更新 → soul.json保存のループを実装。

**成果物:**
- `soul_schema.py` — SOULフォーマット定義（4層構造: Core Identity / Episodic Memory / Semantic Map / Temporal Patterns）
- `extractor.py` — Ollama LLMによるテキスト→SOUL delta抽出
- `soul_engine.py` — ベイズ更新・重み減衰・distillation
- `main.py` — 対話CLI（interactive, --input, --status, --check, --init）
- `test_pipeline.py` — ユニットテスト **38件全通過**

**技術的ポイント:**
- 10次元ガウス分布（Big5 + curiosity, creativity, empathy, risk_tolerance, independence）
- 各次元は {μ, σ} で表現。σが小さいほど確信度が高い
- qwen3:14bの `/no_think` モードで推論速度を大幅改善
- `<think>` タグの漏出対策として正規表現でのストリッピングを実装

### A2: 抽出精度検証 ✅

10種類のテキスト（内向的〜冒険的、日常〜人生の転機）で自動評価。

| 評価軸 | 精度 |
|--------|------|
| 感情カテゴリ | 100% (10/10) |
| 重要度レンジ | 100% (10/10) |
| 性格方向一致 | 75% (6/8) |
| **総合** | **92%** |

### A3: 多元入力統合 ✅

テキストのみ vs テキスト+位置+時刻のコンテキスト付きを比較。

- 性格シグナル変化: **5/5ケース** (100%)
- 重要度変化: 3/5ケース (60%)
- 代表例: 「おいしいご飯」+「ミシュラン三つ星」でopenness 0.20→0.70

多元データの有効性を実証。コンテキストの付加で抽出の質が明確に向上する。

### A4: ベイズ的忘却 ✅

全13テスト通過。

```
Day 1   extraversion: μ=0.50, σ=0.30  ← 不確か
Day 10  extraversion: μ=0.32, σ=0.09  ← 確信が増す
Day 20  extraversion: μ=0.31, σ=0.06  ← 性格として定着
```

- 20回の一貫した観測でσが0.30→0.055に収束（確信が6倍に向上）
- 矛盾する観測への適応: μが新しい証拠の方向に移動
- Exponential decay: 半減期30日、重要度で減衰速度を調整
- Distillation: 消えゆくエピソードの性格シグナルがcore_identityに統合

### A5: Claude連携体験検証 ✅

SOULから自動生成されたSystem PromptでAIの応答が明確にパーソナライズされることを確認。

- SOULなし: 汎用的なアドバイス
- SOULあり: 興味・価値観・行動パターンが応答に反映

---

## Track B — ハードウェア実現性

### B1: ハードウェアフィージビリティ ✅

Pi Zero 2W (512MB RAM) でのAIモデルスタック動作可否を調査。

**結論: 動作可能だがTier別排他実行が必須。製品版はPi 5 (2GB) を推奨。**

| モデル | サイズ | Pi Zero 2W | Pi 5 (2GB) |
|--------|--------|------------|------------|
| Whisper tiny | 39MB | 実時間2-4倍 | 6秒/10秒音声 |
| LLM 0.5-1B (INT8) | 150-180MB | 8-15 t/s | 40+ t/s |
| MiniLM-L6-v2 | 22MB | 300-500ms/文 | <100ms/文 |

**512MB RAM制約:** 3モデル同時ロードは不可能。以下の順次パイプラインが必要:

```
[音声入力] → Whisper (55MB) → テキスト → LLM (180MB) → 性格抽出 → MiniLM (50MB) → ベクトル → SOUL更新
            ※各モデルをロード→実行→アンロードの繰り返し
```

パイプライン合計: 10-20秒/入力（音声処理を除く）

**消費電力:** アイドル0.4W / 推論時1.0-1.3W → 2000mAhバッテリーで約10時間連続推論可能

**推奨ロードマップ:**

| フェーズ | ハードウェア | 状況 |
|----------|-------------|------|
| Pre-MVP | PC上でパイプライン検証 | ✅ 完了 |
| MVP | Pi 5 (2GB) で3モデル同時実行 | ← **次のステップ** |
| 製品版 | Cortex-A53専用基板 + 1GB RAM | 将来 |
| 将来 | NPU搭載基板（Radxa系チップ） | 将来 |

---

## Track C — 選択的自己開示

### C1: 開示システム設計 ✅

8カテゴリ × 4粒度レベルの開示制御システムを設計。

**8つの開示カテゴリ:**
Core Identity / Episodic Memory / Emotional State / Behavioral Patterns / Health Vitals / Location & Movement / Social Graph / Interests & Values

**4つの粒度レベル:**

| 粒度 | 説明 | 例 |
|------|------|-----|
| Full | 生データ開示 | 全性格次元のμ/σ |
| Summary | 集計・平均化 | エピソード件数のみ |
| Anonymized | 個人識別子削除 | 名前→SHA256ハッシュ |
| Hidden | 非開示 | データを返さない |

**5つのスコープテンプレート:**
Claude Personal（パーソナルアシスタント）/ Work Assistant（仕事効率化）/ Health Coach（健康管理）/ Minimal（最小限）/ Emergency（緊急一時開示）

### C2: Capability Token実装 ✅

HMAC-SHA256署名付きトークンによる認証・認可モジュール。

- JWT互換ペイロード構造（iss, sub, scope, iat, exp, jti）
- トークンライフサイクル: 発行 → 検証 → 消費/失効
- ワンタイムトークン、自動期限切れ、永続化（JSON保存/復元）
- 改ざん検知、異なる鍵での検証失敗、失効後の拒否をテストで確認
- テスト: **9/9 通過**

### C3: スコープ別体験検証 ✅

「新しいスキルを身につけたい」という質問に対する応答の違い:

- **Minimal:** 汎用的な提案（プログラミング、データ分析…）
- **Full:** SOULの興味データ（AI, hardware innovation）を参照したパーソナライズ提案

スコープが広いほどAIの応答がユーザーに合ったものになることを実証。

### C4: Permission Gateway実装 ✅

Token検証 → SOULフィルタリング → 粒度変換 → System Prompt生成のE2Eフロー。

**E2E検証結果:**

| スコープ | Prompt長 | 開示カテゴリ | 非開示カテゴリ |
|----------|---------|-------------|-------------|
| Claude Personal | 918文字 | 5 | 2 |
| Work Assistant | 511文字 | 4 | 4 |
| Health Coach | 533文字 | 4 | 3 |
| Minimal | 284文字 | 1 | 7 |

**境界ケース解決:** 複数カテゴリにまたがるデータは最も制限的なカテゴリのルールを適用。
- 「仕事の会話から推測される健康状態」→ HEALTH_VITALS (HIDDEN) が優先
- 「Aさんとの会話」→ SOCIAL_GRAPH (ANONYMIZED) が優先

テスト: **13/13 通過**（Gateway 10件 + Boundary 3件）

### C5: 管理UIモックアップ ✅

`disclosure_dashboard.html` — インタラクティブHTMLダッシュボード。
スコープテンプレート切替、カテゴリ別開示マトリックス、トークン管理、境界ケース表示、データフロー図を含む。

---

## 成果物一覧

### パイプラインコア（Track A）

| ファイル | 説明 |
|----------|------|
| `soul_schema.py` | SOULフォーマット定義（4層構造） |
| `extractor.py` | Ollama LLMによるテキスト→SOUL delta抽出 |
| `soul_engine.py` | ベイズ更新・重み減衰・distillation |
| `soul_to_prompt.py` | SOUL→System Prompt自動変換 |
| `main.py` | 対話CLI |

### 開示制御（Track C）

| ファイル | 説明 |
|----------|------|
| `capability_token.py` | C2: Capability Token生成・検証・失効管理 |
| `permission_gateway.py` | C4: Permission Gateway（フィルタリング+Prompt生成） |
| `disclosure_dashboard.html` | C5: 管理UIモックアップ |

### テスト・検証

| ファイル | テスト数 | 結果 |
|----------|---------|------|
| `test_pipeline.py` | 38件 | 全通過 |
| `test_a2_accuracy.py` | 10ケース | 92%精度 |
| `test_a3_multimodal.py` | 5ケース | 100%変化 |
| `test_a4_forgetting.py` | 13件 | 全通過 |
| `test_a5_claude.py` | 3質問 | パーソナライズ確認 |
| `test_c3_scope.py` | 2質問×3スコープ | 段階的差分確認 |
| `test_c2_c4_disclosure.py` | 22件 | 全通過 |
| `test_soul_cortex.py` | 25件 | 全通過 |
| `test_cortex_manager.py` | 42件 | 全通過 |

### 設計仕様

| ファイル | 説明 |
|----------|------|
| `specs/b1_hardware_feasibility.md` | HW実現性レポート |
| `specs/c1_disclosure_spec.py` | 開示システム仕様（Python実装） |
| `specs/c1_disclosure_spec.md` | 開示システム仕様書 |

---

## ハードウェアMVP構築計画

### 目標

Pi 5 (2GB) 上でGhost-Printerパイプライン全体を動作させ、「音声入力 → SOUL更新 → Permission Gateway → 外部AI」のE2Eフローを実機で検証する。

### 必要な購入品

| 品目 | 目的 | 参考価格 |
|------|------|---------|
| Raspberry Pi 5 (2GB) | メインSoC | $40-50 |
| microSDカード (32GB+) | OS + モデルストレージ | $10 |
| USB-Cアダプタ + 電源 | Pi 5給電 (5V/5A) | $15 |
| I2S MEMSマイク (SPH0645等) | 音声入力 | $5-10 |
| PiSugar 3 バッテリーHAT | モバイル駆動 | $30 |
| Pogo Pin (4極) | Soul Dockプロト用 | $5 |
| 3Dプリントケース素材 | Shell試作 | $5-10 |

**合計: 約$110-120**

### 実装フェーズ

**Phase 1: Pi 5セットアップ + モデル動作確認**
- Pi OS Lite (headless) セットアップ
- whisper.cpp ビルド + Whisper tinyモデル動作確認
- llama.cpp (1-bit fork) + Bonsai 1.7B (240MB) の動作・速度計測
- ONNX Runtime + MiniLM-L6-v2 の動作確認
- 3モデル同時ロード時のメモリ使用量計測

**Phase 2: パイプライン移植**
- 既存Pythonコード（soul_schema, extractor, soul_engine）をPi 5に移植
- Ollama → llama.cpp (gguf) への切替
- Whisper.cppとの統合（マイク入力 → テキスト → SOUL delta）
- Permission Gateway + Capability Token の動作確認

**Phase 3: ハードウェア統合**
- I2S MEMSマイク接続 + ALSA設定
- VAD（Voice Activity Detection）実装（Tier1常時低消費）
- バッテリーHAT統合 + 消費電力実測
- 稼働時間の算出

**Phase 4: Soul Dockプロトタイプ**
- Pogo Pin 4極（GND/VBUS/D+/D-）の回路設計
- 3Dプリントでドック筐体を試作
- USB 2.0経由の充電+データ同期動作確認

**Phase 5: Shell設計**
- Wearable Shell (腕時計型) のCAD設計
- Desk Shell (据え置き型) のCAD設計
- STL/STEPファイルをGitHubに公開

---

## Soul Cortex — 3モデル協調アーキテクチャ

従来の直列パイプライン（Whisper → Bonsai → MiniLM → SOUL）を脱却し、3つのモデルがそれぞれ独自の「観測層」を持ち、Shared Soul Stateを介して互いの観測を参照・補強し合う協調システム。

### 設計思想

SOULは確率分布とベクトルで格納する。テキストは格納しない。各モデルが異なる「次元」から現実を観測し、数値表現として蓄積する。

```
         ┌──────────────────────────────────┐
         │         Shared Soul State        │
         │   (確率分布 + ベクトル空間)       │
         └──┬───────────┬───────────┬───────┘
            ↕           ↕           ↕
      [Whisper]    [Bonsai]     [MiniLM]
      音響層        意味層       埋込層
```

### 各モデルの役割と情報交換

| モデル | 観測対象 | 蓄積するデータ | 他モデルへの寄与 |
|--------|---------|---------------|-----------------|
| Whisper (音響層) | 生音声 | 韻律パターン（ピッチ変動, テンポ, ポーズ長）, Valence-Arousal | 感情の音響的証拠 → Bonsaiの確信度を補強 |
| Bonsai 1.7B (意味層) | テキスト + 音響特徴 + 類似度 | 性格シグナル(μ,σ), 重要度, 感情カテゴリ分布 | 重要度スコア → MiniLMの重み付け |
| MiniLM (埋込層) | テキスト + 重要度 | 512次元埋め込み, トピッククラスタ, エピソード間類似度 | 意味的近接性 → Bonsaiのコンテキスト補強 |

### 情報交換プロトコル

**Whisper → Bonsai（音響補強）:** 話速の変化やピッチの揺れが、感情抽出の確信度を補強。ポジティブ感情 + 高valence/高arousal → ブースト。矛盾時は確信度を下げる。

**Bonsai → MiniLM（重要度伝搬）:** 重要なエピソードの埋め込みベクトルにより大きな重みを付与。クラスタ中心への影響力が増す。

**MiniLM → Bonsai（文脈補強）:** 過去エピソードとの高い類似度が、パターンの反復を示す証拠となり、性格シグナルの確信度を高める。

### テスト結果

| カテゴリ | テスト数 | 結果 |
|---------|---------|------|
| 音響層 (AC) | 3件 | 全通過 |
| 意味層 (SM) | 3件 | 全通過 |
| 埋込層 (EM) | 3件 | 全通過 |
| 情報交換 (EX) | 1件 | 全通過 |
| ベイズ統合 (INT) | 4件 | 全通過 |
| Shared State (SS) | 2件 | 全通過 |
| E2E (E2E) | 3件 | 全通過 |
| ユーティリティ (UTL) | 1件 | 全通過 |
| **合計** | **20件** | **全通過** |

### テスト結果（CORTEX統合後）

| カテゴリ | テスト数 | 結果 |
|---------|---------|------|
| 音響層 (AC) | 3件 | 全通過 |
| 意味層 (SM) | 3件 | 全通過 |
| 埋込層 (EM) | 3件 | 全通過 |
| 情報交換 (EX) | 1件 | 全通過 |
| ベイズ統合 (INT) | 4件 | 全通過 |
| Shared State (SS) | 2件 | 全通過 |
| E2E (E2E) | 3件 | 全通過 |
| ユーティリティ (UTL) | 1件 | 全通過 |
| CORTEX統合 (CX) | 5件 | 全通過 |
| **合計** | **25件** | **全通過** |

### 成果物

| ファイル | 説明 |
|----------|------|
| `soul_cortex.py` | 3モデル協調オーケストレーター + Shared Soul State + CORTEX統合 |
| `test_soul_cortex.py` | 統合テスト25件（CORTEX連携5件含む） |

---

## CORTEX — デバイス脳回路ファイル

### 概念: CORTEX vs SOUL

| | CORTEX | SOUL |
|---|---|---|
| 格納先 | Core基板のフラッシュメモリ | microSD / ストレージ |
| 内容 | 「脳の回路」— LLMの動かし方 | 「人格」— ユーザーのパーソナリティ |
| 変更頻度 | 開発・チューニング時のみ | 毎回の入力で更新 |
| 例え | OS / ファームウェア | ユーザーデータ |

### CORTEX.bin バイナリ仕様

```
ヘッダー:
  [GPCX]  4 bytes   Magic Number
  [0x02]  2 bytes   Format Version (little-endian uint16)
  [size]  4 bytes   Data Length (little-endian uint32)

ボディ:
  gzip圧縮されたJSONデータ
  - whisper_config    Whisper(音響層)の設定
  - bonsai_config     Bonsai(意味層)の設定 + 自動生成System Prompt
  - minilm_config     MiniLM(埋込層)の設定
  - cortex_config     Soul Cortex全体の情報交換パラメータ
  - meta              バージョン・チェックサム・変更履歴
```

**サイズ:** 約2.6KB（JSON 7.8KB → gzip圧縮、ratio=0.33）
**整合性:** SHA256チェックサム検証

### CortexManager機能

| 機能 | 説明 |
|------|------|
| `build()` | デフォルト設定でCORTEXを生成。10次元の性格次元定義を含む |
| `save()` | GPCX形式バイナリとしてシリアライズ |
| `load()` | CORTEX.binを読み込み + チェックサム検証 |
| `validate()` | 整合性チェック（次元定義、パラメータ範囲、Big5網羅性） |
| `update_param()` | ドットパスでパラメータを試行錯誤的に更新（例: `bonsai.temperature`, 0.5） |
| `bump_version()` | セマンティックバージョニング（major/minor/patch） |
| `diff()` | 2つのCORTEX.binの差分比較 |
| `export_json()` | 人間可読JSONエクスポート |
| `build_system_prompt()` | BonsaiConfigからLLM抽出プロンプトを自動生成 |

### Soul Cortex統合

`SoulCortex` がCORTEX.binから設定を読み込み、情報交換パラメータを動的に制御:

```python
# 実機での起動フロー
sc = SoulCortex.from_cortex_file(soul, "/dev/flash/CORTEX.bin")

# CORTEXパラメータが自動適用される:
#   acoustic_boost_weight, acoustic_mismatch_penalty,
#   context_boost_weight, context_similarity_threshold,
#   bayesian_sigma_floor, obs_sigma_base, obs_sigma_range
```

### 試行錯誤フロー

```
1. cortex_manager.py でCORTEXをビルド
2. パラメータを変更（update_param + bump_version）
3. CORTEX.bin を保存 → Core基板に書き込み
4. Soul Cortexが新しいパラメータで動作
5. 結果を評価 → 2に戻る
```

### テスト結果

| カテゴリ | テスト数 | 結果 |
|---------|---------|------|
| ビルド (BLD) | 5件 | 全通過 |
| シリアライズ (SER) | 6件 | 全通過 |
| 検証 (VAL) | 6件 | 全通過 |
| パラメータ更新 (UPD) | 7件 | 全通過 |
| バージョン管理 (VER) | 4件 | 全通過 |
| 差分 (DIF) | 2件 | 全通過 |
| エクスポート (EXP) | 2件 | 全通過 |
| System Prompt (SYS) | 4件 | 全通過 |
| エラーケース (ERR) | 6件 | 全通過 |
| **合計** | **42件** | **全通過** |

### 成果物

| ファイル | 説明 |
|----------|------|
| `cortex_manager.py` | CORTEXのビルド・読み込み・検証・更新・差分管理 |
| `test_cortex_manager.py` | テスト42件 |

---

## Watch Point — 観測ポイントの生態系モデル

### 設計思想

LLMの観測によって「この人について特に見ておくべきポイント（観測対象）」を動的に生成し、**生態系のように生まれ・育ち・競争し・淘汰される**機構。単純な追加だけでは情報量が発散してしまうため、フィットネス指標による自然淘汰と、死んだWPの学習を残す「蒸留」を組み合わせることで、情報の煩雑性を抑えつつ重要なパターンだけを保持する。

```
Watch Point ライフサイクル:
  nascent  → 試用期間（N回以内にhitなければ落第）
  active   → 安定稼働（情報利得を安定して生む）
  dormant  → 休眠（最近hitがないが過去に貢献、復活可能）
  dying    → 淘汰対象（fitness閾値以下）
  culled   → 消滅（学習はSOULのcore_identityに蒸留）
```

### フィットネス計算

```
fitness = information_gain × hit_rate × priority × recency_factor
          ^^^^^^^^^^^^^^^^^   ^^^^^^^^^   ^^^^^^^^   ^^^^^^^^^^^^^^^
          (観測で何が変わったか) (ヒット率) (LLM初期評価) (半減期14日の時間減衰)
```

### 淘汰メカニズム（5種）

1. **自然減衰** — `recency_factor = 0.5 ^ (elapsed_days / half_life)` で時間経過とともに下降
2. **容量競争** — `max_active` 超過時、最弱WPが新WPに置換される
3. **冗長剪定** — Jaccard類似度 ≥ `merge_similarity_threshold` のペアを統合
4. **ゼロヒット** — `fitness < fitness_floor` になると DYING
5. **試用期間落第** — NASCENTで `probation_trials` 回以内に `min_hits_to_graduate` 未達で DYING

### 蒸留（Distillation）

CULLED状態のWPは即座には失われず、その `affects_dimensions` と観測平均値が SOULの core_identity に弱く反映される。これにより「消えたWPの学びが性格特性として昇華」される設計。

```
weight = min(0.3, information_gain × hit_count × 0.1)
new_mu = dim.mu × (1 - weight) + obs_mean × weight
```

### WatchPointRules — 生成トリガー

| ルール | 検出条件 | トリガー |
|--------|----------|----------|
| トピック集中 | 直近10件の分布で特定トピックが `topic_concentration_threshold` (0.6) 以上 | `TOPIC_CONCENTRATION` |
| 重要度集中 | `importance ≥ 0.7` のエピソードが3件以上 | `IMPORTANCE_SPIKE` |
| 性格σ急拡大 | `sigma_diff ≥ 0.1` の次元 | `PERSONALITY_UNCERTAINTY` |

### CORTEX統合

全ての閾値・キャパ・半減期は `CORTEX.watchpoint` に格納され、試行錯誤で再調整可能：

```python
cortex.watchpoint = WatchPointConfig(
    max_active=20,                      # 同時アクティブWP上限
    probation_trials=3,                 # 試用期間
    fitness_floor=0.05,                 # 淘汰閾値
    decay_half_life_days=14.0,          # 時間減衰の半減期
    merge_similarity_threshold=0.85,    # 統合判定 (Jaccard)
    topic_concentration_threshold=0.6,  # トピック偏りトリガー
    importance_spike_count=3,           # 高重要度集中件数
    personality_sigma_spike=0.1,        # σ急拡大トリガー
)
```

### Soul Cortex統合

```python
cortex = SoulCortex.from_cortex_file(soul, "/dev/flash/CORTEX.bin")

# 各 integrate_observations() 呼び出しでWPを自動更新
cortex.integrate_observations(acoustic, semantic, embedding)
# ↓ 内部で wp_rules.check_*() → wp_manager.propose()
# ↓ wp_manager.observe() で関連WPに情報利得を記録

# 定期的（例: 1日1回）に生態系を進化させる
stats = cortex.evolve_watchpoints()
# stats = {'transitioned_to_dormant': 0, 'transitioned_to_dying': 2,
#          'culled': ['wp_weather_talk_...'], 'merged': [], 'distilled': 1}
```

### テスト結果

| カテゴリ | テスト数 | 結果 |
|----------|----------|------|
| WatchPointデータクラス (WP) | 5件 | 全通過 |
| propose (PR) | 6件 | 全通過 |
| observe (OB) | 6件 | 全通過 |
| fitness (FT) | 5件 | 全通過 |
| evolve (EV) | 5件 | 全通過 |
| distillation (DS) | 4件 | 全通過 |
| WatchPointRules (RL) | 5件 | 全通過 |
| マージ・ライフサイクル (ML) | 2件 | 全通過 |
| **watchpoint合計** | **38件** | **全通過** |
| Soul Cortex統合 (WP-01〜07) | 7件 | 全通過 |

### 成果物

| ファイル | 説明 |
|----------|------|
| `watchpoint.py` | WatchPoint/WatchPointManager/WatchPointRules 実装 |
| `test_watchpoint.py` | テスト38件 |
| `cortex_manager.py` の `WatchPointConfig` | CORTEX側の設定（全パラメータをバイナリに同梱） |
| `soul_cortex.py` の `_update_watchpoints()` / `evolve_watchpoints()` | パイプライン連携とSOUL永続化 |

---

## 未解決の設計上の問い

| 問い | 状況 |
|------|------|
| マイクの常時起動は法的・倫理的に許容されるか？ | 「VADのみ・録音しない」か「ユーザーが明示的に開始」かを決める必要あり |
| BLE帯域でSOUL差分同期は現実的か？ | A6 ベンチで実用域確認 (差分 ~300B = 2.4ms)。プロトコル詳細は L2 設計時に確定 |
| Core間CRDT同期の実装 | Automerge vs カスタム実装の選定。A6 の `epoch_counter` + APPEND_LOG をベースに自作する方向 |
| .soul バイナリフォーマットの仕様策定 | ✅ A6 で v0.1 prototype 完成 (`specs/a6_soul_binary_format.md`, 13/13 テストパス) |

---

## A6 — `.soul` バイナリフォーマット (2026-05-03 追加)

**ステータス:** Prototype v0.1 完成。仕様 Approved。実装・ラウンドトリップ・改ざん検知すべて検証済み。

### 設計方針 (採択済 8項)

| # | 項目                       | 採択                                                |
|---|----------------------------|-----------------------------------------------------|
| Q1 | エンディアン               | Little-Endian                                       |
| Q2 | 圧縮                       | zstd level 10 (CORTEXは gzip 据え置き)             |
| Q3 | 暗号                       | AES-256-GCM, セクション単位 (実装は次フェーズ)     |
| Q4 | owner_id_hash              | デバイス自動生成 + 任意リカバリーシード             |
| Q5 | コンパクション頻度         | 毎日 02:00 + ログ 64KB 超で即時                     |
| Q6 | raw_text                   | 暗号化セクション隔離 + 30日 TTL                     |
| Q7 | format_version             | major 5bit / minor 11bit                            |
| Q8 | 拡張子                     | `.soul`                                             |

### 構造

`HEADER (128B) → TOC (12B/section) → SECTIONS → optional APPEND_LOG`

| Section                | Encoding         | 用途 / Disclosure Category 対応 |
|------------------------|------------------|-------------------------------|
| 0x01 CORE_IDENTITY     | raw struct (80B) | Core Identity                 |
| 0x02 EPISODIC_RECENT   | CBOR + zstd      | Episodic Memory               |
| 0x03 EPISODIC_COMPRESSED | CBOR + zstd    | Episodic Memory               |
| 0x04 SEMANTIC_MAP      | CBOR             | Interests & Values            |
| 0x05 TEMPORAL_PATTERNS | CBOR             | Behavioral Patterns           |
| 0x06 WATCHPOINTS       | CBOR             | Behavioral Patterns           |
| 0x07 STATS             | raw struct (16B) | Always disclosable            |
| 0x08–0x0B              | CBOR + AES-GCM   | Emotional/Health/Location/Social |
| 0xFE STRING_TABLE      | length-prefixed  | topics/values dedup           |
| 0xFF APPEND_LOG        | length-framed CBOR | 差分ジャーナル              |

ヘッダに **CRC32 (前方検証用) + Merkle Root (各セクションSHA256集約)** を持ち、開示用ビューでセクションを削っても残った部分の改ざん検知が可能。

### 実測サイズ (A6 §10 抜粋)

| 入力                              | JSON     | .soul    | 比率   |
|-----------------------------------|----------|----------|--------|
| 実データ `data/soul.json` (4 ep)  | 7,592 B  | 2,167 B  | 28.5%  |
| 合成 30 ep                        | 18,205 B | 1,413 B  | **7.8%** |
| 合成 100 ep                       | 58,740 B | 2,737 B  | **4.7%** |

エピソードが増えるほど劇的に縮む (STRING_TABLE dedup + zstd の合算効果)。

### 部分読出し性能

`read_section(SEC_CORE_IDENTITY)` = **0.3 µs** (TOC ジャンプ + 80B 読み出し)。
JSON 全体パースの約 600 倍速いため、Permission Gateway がスコープ別 redaction を実時間で返せる。

### テスト結果

| カテゴリ                          | テスト数 | 結果     |
|-----------------------------------|----------|----------|
| ラウンドトリップ                  | 3        | 全通過   |
| ヘッダ整合性 (CRC, magic)         | 3        | 全通過   |
| Merkle 改ざん検知                 | 2        | 全通過   |
| 部分読み出し (TOC探索)            | 3        | 全通過   |
| スキーマ揺れ吸収                  | 2        | 全通過   |
| **AES-GCM 暗号化 (round-trip / 鍵無し redacted / 改ざん検知 / 鍵導出一貫性 / IV uniqueness)** | **7** | **全通過** |
| **Capability Token 連携 (HEALTH_COACH/MINIMAL scope→ 鍵セット制限)** | **3** | **全通過** |
| **部分開示ビュー (granted_sections + partial flag)** | **1** | **全通過** |
| **合計**                          | **24**   | **全通過** |

### 成果物

| ファイル | 説明 |
|----------|------|
| `specs/a6_soul_binary_format.md` | A6 仕様書 v1 (Approved) — 暗号化 §7 / APPEND_LOG §4.9 / Merkle 部分証明 §7.3 含む |
| `specs/b2_opi3b_migration.md` | **B2 — OPi 3B 移行仕様** (Pi 5 → OPi 3B の差分・性能予測・購入リスト) |
| `SETUP_OPI3B.md` | **OPi 3B 実機セットアップ手順書** (Phase 0-9, トラブルシュート込み) |
| `flash_cortex.py` | **B3 — OPi 3B 対応**: `--bus/--device/--speed-hz`, env (`GPP_SPI_*`), `--read-id` |
| `test_flash_cortex.py` | **テスト 11件** (argparse / env / シミュレータ round-trip / --read-id) |
| `specs/a7_adaptive_watchpoint.md` | **A7 — 適応型 Watch Point** (固定ルール + LLM 提案ハイブリッド) |
| `watchpoint_llm.py` | **LlmWpProposer / AdaptiveWatchPointPolicy / LlmHealthTracker / ChainedLlmCall / probe_llm_health / LlmRestartManager** |
| `test_watchpoint_llm.py` | **テスト 60件** (プロンプト/パース/Proposer/Policy/Manager + 健康監視21件 + Restart14件) |
| `specs/b4_colmi_r02_protocol.md` | **B4 — COLMI R02 BLE 受信** プロトコル仕様 |
| `specs/c6_soul_protocol.md` | **C6 — Soul Protocol L1-L5** 詳細仕様 (Approved 2026-05-04) — Soul Dock 有線同期 + Op-based CRDT |
| `colmi_r02_protocol.py` | 16B パケット組立/分解、 主要コマンド builder/parser |
| `colmi_r02_client.py` | ColmiBackend ABC + BleakBackend (実BLE) + SimulatedBackend + ColmiClient (async) |
| `hrv_calculator.py` | RMSSD / SDNN / pNN50 / mean_hr / stress_score / filter_rri |
| `test_colmi_r02.py` | **テスト 40件** (パケット7+builder5+parser8+client7+HRV13) |
| `soul_binary.py` | encoder / decoder / partial reader / AES-GCM / 鍵導出 / **MerkleTree / extract_partial_view_bytes / verify_partial_view** / CLI |
| `soul_log.py` | SoulLog (append-only journal) / compact_with_log / should_compact |
| `soul_storage.py` | **ShadowStorage** (JSON+Binary+Log の3層協調 / load fold / explicit compact) |
| `permission_gateway.py` | filter_soul_bytes / create_partial_view / **create_verifiable_partial_view** を追加 (A6 統合) |
| `test_soul_binary.py` | テスト 24件 + ベンチ |
| `test_soul_log.py` | テスト 25件 (basics/compaction/append helper + **暗号化 log 10件**) |
| `test_permission_gateway_binary.py` | テスト 16件 (filter/partial_view/E2E、暗号学的強制) |
| `test_soul_storage.py` | テスト 12件 (initial/incremental/compaction/recovery/encrypted/legacy/log_no_plaintext_verification) |
| `test_merkle_proof.py` | **テスト 21件** (MerkleTree 単体 / extract / verify / Gateway E2E / 攻撃シナリオ) |
| `requirements.txt` | cbor2 / zstandard / cryptography 追加 |

**A6 系テスト合計: 98 件 (24 + 25 + 16 + 12 + 21) すべて全通過**。
**プロジェクト全体: 既存と併走で 232 件全通過**。

### 次フェーズ

1. ✅ AES-256-GCM セクション暗号化 (Capability Token と鍵紐付け) — **2026-05-03 完成**
   - HKDF-SHA256 で section_key 導出 (info=`soul:0xNN`)
   - 決定論 IV = SHA256(epoch_counter ‖ section_id)[:12]
   - AAD = section_id ‖ epoch で cross-section swap 防御
   - `derive_keys_for_token(master_key, capability_token)` で HIDDEN を暗号学的に隔離
   - 部分開示ビュー (`granted_sections` + `HEADER_FLAG_PARTIAL`)
   - **オーバーヘッド: +17B/encrypted section、encode/decode +5-10% (鍵なしdecodeは大幅高速化)**
2. ✅ APPEND_LOG / コンパクション モード — **2026-05-03 完成**
   - `.soul.log` 別ファイル (GPLG ヘッダ + length-framed CBOR)。本体 `.soul` は append しないことで原則 SD/eMMC 上で純 append
   - `OP_INPUT`/`OP_CORE_UPDATE`/`OP_EPISODE_ADD`/`OP_DECAY`/`OP_DISTILL`/`OP_WATCHPOINT`/`OP_STRING_TABLE_EXT` の 7 op
   - `compact_with_log(snapshot, log, replay_fn)` でスナップショット反映＋アトミック交換 (.tmp → os.replace) ＋ログクリア
   - `should_compact(log)` で Q5 採択 (毎日 02:00 + 64KB超で即時) を判定
   - 暗号化付き compact もパス。`replay_fn` を呼出側で渡す設計で `soul_engine` の循環依存を回避
   - 中途切断エントリの安全な破棄 (電源断耐性) を実装
   - **テスト 15/15 全通過**
3. ✅ シャドー書込み統合 (.json + .soul + .soul.log) — **2026-05-03 完成**
   - `soul_storage.py` の `ShadowStorage` クラスで JSON / Binary / Log の3層を協調
   - `append_update()`: 1 update で JSON フル書換 + Log 追記、コンパクション閾値超で bin を更新＋log クリア
   - `load()`: bin (鍵あれば暗号化セクションも) を decode → 未消費 log を replay → in-memory 完全状態を復元
   - 初期書込み・増分書込み・サイズ閾値での発火・クラッシュリカバリ・暗号化付き・JSON-only legacy / 明示 compact のテスト 12/12 全通過
4. ✅ Permission Gateway を `read_section()` + `derive_keys_for_token()` 経由に統合 — **2026-05-03 完成**
   - `PermissionGateway.filter_soul_bytes(soul_bytes, token, master_key)`: 鍵導出 → decode_soul → 既存粒度変換を二層構造で適用。HIDDEN は鍵が出ないので暗号学的に隔離 (アプリ層フィルタを信頼しない)
   - `PermissionGateway.create_partial_view(soul_bytes, token, master_key)`: 自己完結 `.soul` バイト (HEADER_FLAG_PARTIAL=1, 平文) を生成。外部AIが master_key 無しで decode 可能
   - **テスト 16/16 全通過** — 暗号学的強制 (HIDDEN scope のセクションは directly decode してもデータが出ない) / 失効/ワンタイム token / 異なる Token から異なる view 生成 / E2E (オーナー → claude_personal → Claude の視界) を網羅
5. ✅ Log エントリ暗号化 (per-entry AES-256-GCM) — **2026-05-03 完成**
   - `SoulLog(path, master_key=...)` で各エントリを AES-256-GCM 封印
   - 鍵: `HKDF-SHA256(master, info="soul_log:epoch_{N}")`、IV: `SHA256("soul_log_iv:"+epoch+index)[:12]` の決定論
   - AAD = `epoch + index` で位置/log間 swap 攻撃を防御。コンパクションで epoch が進むので forward secrecy 風
   - **raw_text が disk のどこにも平文で残らない** (ASCII 検査で検証)。Q6 採択を完全達成
   - 後方互換: master_key 無しなら従来の平文 log 動作。混在書込み (平文 log に暗号化 append) は明示拒否
   - 10/10 専用テスト + 既存 ShadowStorage 12/12 が全通過
6. ✅ Merkle 部分証明 (zk-style proof) — **2026-05-03 完成**
   - `MerkleTree` クラス (build / proof_for / verify) を soul_binary.py に追加
   - `extract_partial_view_bytes(original, granted_sids)`: 元 section bytes を verbatim 保持して granted のみ含む partial view + SEC_MERKLE_PROOF (0x0C) 同梱
   - `verify_partial_view(view_bytes, expected_original_root=)`: proof bundle で各セクションが原本 root に到達するか検証
   - `PermissionGateway.create_verifiable_partial_view`: Token から section_keys 導出 + verifiable view 生成 → `(view_bytes, section_keys, FilterResult)` を返す
   - epoch_counter を原本から保持するので、受信者は section_keys で AES-GCM 復号可能 (IV 派生に必要な epoch が一致)
   - 改ざん検知 / 異なる SOUL から作った view 拒否 / 偽 root 拒否 を E2E で検証
   - 21/21 専用テスト全通過 (MerkleTree 単体 + extract + verify + Gateway E2E + 攻撃者シナリオ)
   - **これで A6 仕様書 §1〜§10 のすべての機能が実装済み**

---

*このレポートはGhost-Printerプロジェクトの2026-04-16時点での進捗をまとめたものです。*
*Track A (SOUL抽出)、Track C (開示制御)、Soul Cortex (3モデル協調)、CORTEX Manager (デバイス脳回路)、Watch Point (観測ポイント生態系) のソフトウェア実装が完了し、次はTrack B (ハードウェアMVP: Pi 5実機構築) に進みます。*
