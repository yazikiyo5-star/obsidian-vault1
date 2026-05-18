# Ghost-Printer — プロジェクト引き継ぎドキュメント

**ステータス:** Concept / Pre-MVP  
**作成日:** 2026-04-08  
**目的:** Coworkへの作業引き継ぎ。設計の背景・決定済み事項・次のアクションを網羅する。

---

## 1. プロダクト概要

### プロダクト名
**Ghost-Printer**（ゴースト・プリンター）

### コンセプト
日常生活から取得した多元的なデータを「SOUL」フォーマットに変換・蓄積し、AIが読み込みやすい形で整形するパーソナルデバイス。

- AIとの対話を「毎回初対面」から「長年の付き合い」に変える
- データはデバイス上でローカル処理・保存（クラウド不要）
- ユーザーが「誰に・何を・どこまで」見せるかを選択的に制御できる
- オフラインでも常時稼働し続けること

### 解決する問題
| 現在の問題 | Ghost-Printerの解決 |
|---|---|
| AIへのインプットが一元的・瞬間的 | 音声・行動・感情・位置など多元的データを複合的に蓄積 |
| AIは毎回「初対面」 | 長期的な文脈・価値観・記憶をAIに渡せる |
| データの主権が自分にない | 完全ローカル処理・選択的自己開示で自分がコントロール |

---

## 2. SOUL フォーマット

### 基本設計思想

**人間が読む必要はない。AIが読めれば良い。**

人間可読なJSON（`{"mood": "calm"}`）ではなく、確率分布・埋め込みベクトル・統計モデルで表現する「AI-native」フォーマット。情報密度が桁違いに高く、忘却もベイズ的に自然に実現できる。

### SOULの4層構造

```
soul.soul (バイナリフォーマット)
├── HEADER              固定長 128bytes（バージョン・所有者ハッシュ・次元数）
├── CORE IDENTITY       多次元ガウス分布（128dim）― 安定した性格・価値観
├── EPISODIC MEMORY     3層構造のエピソード記憶
│   ├── recent[]        直近30日: vec[512] + 重要度weight
│   ├── compressed[]    30〜180日: GMM centroidsにクラスタリング
│   └── distilled       180日以上: personality priorに溶け込む（削除）
├── SEMANTIC MAP        興味分布（Dirichlet, 512dim）・人間関係グラフ
└── TEMPORAL PATTERNS   行動の時系列（隠れマルコフモデル・サーカディアンリズム）
```

### ベイズ的忘却（重要な設計原則）

「記憶を消す」のではなく「エピソードが性格に溶ける」設計。

```
Day 1   introversion: μ=0.72, σ=0.28  ← 不確か
Day 30  introversion: μ=0.72, σ=0.14  ← 確信が増す
Day 180 introversion: μ=0.72, σ=0.06  ← 性格として定着
→ エピソード削除。SOULファイルはむしろ軽くなる。
```

証拠が積み重なるほど `σ`（不確かさ）が縮小する。低重要度のエピソードは時間減衰（exponential decay）で重みが下がり、personality priorに統合されて消える。

### ローカルモデルスタック（完全オフライン）

| 役割 | モデル | サイズ |
|---|---|---|
| 音声→テキスト | Whisper tiny | 39 MB |
| 意味抽出 / 重要度判定 | Bonsai 1.7B | 240 MB |
| 埋め込みベクトル生成 | MiniLM-L6 | 22 MB |
| オフライン地名変換 | Nominatim + OSMデータ | 〜200 MB |

**合計約500MB。Raspberry Pi 5（2GB）で余裕で動作する。**

### 処理パイプライン

```
[多元センサー入力]
    ↓ Tier1: 常時低消費（VAD・IMU・GPS polling）
    ↓ Tier2: トリガー起動（Whisper文字起こし・バイタル読み取り）
    ↓ Tier3: バッチ処理（Bonsai意味抽出 → MiniLM埋め込み → SOUL更新）
[.soul ファイル]
    ↓ Permission Gateway
[外部AI（Claude等）]
```

**「重要な瞬間」の検知トリガー（Tier1のコア）:**
- 音声アクティビティ（VAD）が10秒以上継続
- GPS/BLEビーコンが200m以上変化
- 心拍が安静時から15%以上変化
- IMUの活動状態が遷移
- ユーザーの手動入力（最高優先度・重要度×1.5）
- 就寝/起床の検知（日次バッチ処理のトリガー）

---

## 3. ハードウェア設計

### Core（販売品）― 最小機能セット

```
SoC:         Cortex-A53 class（Raspberry Pi Zero 2W相当）
RAM:         512MB〜1GB
Storage:     eMMC 8GB
Battery IC:  込み（Pogo Pin経由で充電）
BLE:         5.0（Core間・外部デバイスとの通信）
Microphone:  I2S MEMS 1基（Tier1 VAD常時起動）
Connector:   USB-C または Pogo Pin ×4（USB 2.0互換）
Display:     なし
Camera/GPS:  なし → Shellに委ねる
```

**設計哲学: 機能を絞ることが攻撃面の縮小になる。信頼の根拠はシンプルさにある。**

### Shell（OSS / 3Dプリンタ対応）

| フォームファクター | コネクタ | 追加可能センサー |
|---|---|---|
| Wearable（腕時計・ペンダント） | Pogo Pin ×4 | 心拍・IMU・GPS |
| Desk（据え置き） | USB-C | 広角マイク・環境センサー |
| Car（車内常設） | USB-C | 加速度・GPS |
| Custom | Shellが規定 | 任意 |

- STL / STEP / KiCadファイルをGitHubで公開
- Shell取り付け規格（機械的・電気的I/F）をOSS仕様書として公開
- Printablesにデザインを投稿してコミュニティを形成

### Soul Dock（充電 = 同期 = 認証）

複数のCoreを差し込む据え置きハブ。**毎晩ドックに置くだけで充電・同期・バックアップが完了する。**

- USB 2.0ハブIC内蔵（Pogo Pin 4極: GND・VBUS・D+・D−）
- 新しいCoreをドックに挿すだけで信頼確立が完了
- ドック自体をHome Coreとして機能させる（据え置きセンサー兼任）

### Core間の信頼確立（有線ファースト）

```
STEP 1: 物理接続（USB-C or Pogo Dock）
    → 物理的所持が「同じ人のデバイス」の証明になる
STEP 2: ECDH鍵交換（有線上で実行）
    → 中間者攻撃が物理的に不可能
STEP 3: 初回フルSOULマージ（USB 2.0: 最大480Mbps）
以降: BLEで差分自動同期（信頼済みのため無操作）
```

**「挿す = 信頼」という直感的UX。クラウド認証・パスワード・QRコード不要。**

---

## 4. Soul Protocol（オープン仕様）

Core同士・サードパーティアプリ・外部AIが接続できるオープンスタック。ActivityPubのPersonal Identity版として仕様公開を想定。

| レイヤー | 名称 | 役割 | 技術 |
|---|---|---|---|
| L5 | Plugin Layer | 任意デバイス・アプリのSOUL寄与 | soul-plugin spec |
| L4 | Merge Layer | 複数観測のBayesian fusion統合 | CRDT + Bayes update |
| L3 | Permission Layer | スコープ制御 | Capability Tokens |
| L2 | Sync Layer | SOUL fragment差分同期 | delta-sync / BLE GATT |
| L1 | Discovery Layer | 近傍Core発見 | BLE advertising |

### CRDTによる競合なしマージ

複数のCoreが同時にSOULを更新しても自動マージされる。オフライン中の更新も矛盾なく統合される。

```python
# WearableCoreの更新
soul.personality["anxiety"].observe(0.71, weight=0.6)
# DeskCoreの更新（同時）
soul.personality["anxiety"].observe(0.68, weight=0.4)
# 自動マージ結果（競合なし）
# μ = 0.6×0.71 + 0.4×0.68 = 0.698
```

### Pluginの3種類

- **Source Plugin**: 新しいデータ源をSOULに追加（Spotify・スマート家電・カスタムセンサー等）
- **Lens Plugin**: SOULの異なる解釈・切り口を提供（健康特化・仕事効率・人間関係ビュー等）
- **Bridge Plugin**: 外部AIやアプリへの接続（Claude System Prompt自動生成・Notionエクスポート等）

---

## 5. 選択的自己開示

### 基本原則

誰に・何を・どこまで見せるかをユーザーが決める。開示は**カテゴリ・期間・粒度**の3軸で制御。

### 開示カテゴリと制御レベル

| カテゴリ | 制御例（Claude Personal） |
|---|---|
| 性格・価値観 | Full（コアアイデンティティを開示） |
| エピソード記憶 | 直近90日のみ |
| 行動パターン | Full |
| バイタル・健康 | サマリーのみ |
| 位置・行動履歴 | 非開示 |
| 人間関係グラフ | 匿名化（名前なし・関係性のみ） |

### Permission API

```
GET /soul/summary          認証不要（公開サマリー）
GET /soul/identity         Token必須（コアアイデンティティ）
GET /soul/memory?scope=    スコープ制限付きメモリ
GET /soul/context/now      直近の状態のみ
```

### Claude連携のSystem Prompt（自動生成イメージ）

```
あなたは {name} のパーソナルアシスタントです。
以下はその人のSOULデータです（開示スコープ内のみ）：

personality: {soul.identity}
recent_context: {soul.context}
patterns: {soul.patterns}

開示スコープ外の情報は含まれていません。
```

### 管理UI（スマホアプリ）

Coreに画面がないため、開示設定はスマホアプリが担う。最初に必要な3機能:
1. AIごとのスコープ設定（カテゴリ・期間・粒度）
2. 一時的な共有（今日だけ / この会話だけ）
3. 緊急停止（全開示を即時取り消し）

---

## 6. ビジネスモデル

| 要素 | 内容 |
|---|---|
| Core収益 | ハードウェア販売（Soul Chip + Soul Dock） |
| Shell | OSS・コミュニティ主導（収益化しない） |
| エコシステム | SOUL APIへのアクセスで将来のプラットフォーム収益 |
| コミュニティ | Shellデザイン投稿がそのままマーケティングになる |

**参照モデル:** Framework Laptop（Core販売 + OSS修理・拡張）

---

## 7. 次のアクション（3トラック）

### Track A — SOUL抽出方法の実験（最優先）

**目的:** ハードウェア不要・PC上で「SOULは本当に使えるフォーマットになるか」を検証する。

| # | 実験内容 | 期間 |
|---|---|---|
| A1 | 手動テキスト入力 → Bonsai意味抽出 → soul.json書き込みのループを動かす | Week 1-2 |
| A2 | 抽出プロンプトの精度検証（「カフェで一人でコーヒー」→「introversion: 0.72」が出るか） | Week 2-3 |
| A3 | 多元インプット統合実験（音声+位置+時刻を束ねたコンテキストとの精度比較） | Week 3-4 |
| A4 | ベイズ的忘却アルゴリズムの実装と検証 | Week 4-5 |
| A5 | SOULをSystem Promptに変換 → Claudeに読み込ませて体験検証 | Week 5-6 |

**A1のスタート地点（最初のコード）:**

```python
# 最初に動かすべきパイプライン
# 入力: テキスト（日記・メモ）
# 出力: soul.json の更新

import json
from datetime import datetime

def extract_soul_delta(text: str, context: dict) -> dict:
    """
    Bonsaiへのプロンプト:
    以下の入力から、この人物の性格・感情・重要度を抽出し、
    JSON形式で返してください。
    
    入力: {text}
    コンテキスト: {context}
    
    出力形式:
    {
      "importance": 0.0-1.0,
      "emotion": {"name": str, "intensity": 0.0-1.0},
      "personality_signals": [{"dimension": str, "value": 0.0-1.0, "confidence": 0.0-1.0}],
      "summary": str
    }
    """
    # TODO: Bonsai / Ollama API呼び出し
    pass

def update_soul(soul_path: str, delta: dict):
    # TODO: Bayesian update of soul.json
    pass
```

### Track B — 物理的な基盤・製品の設計

| # | 作業内容 | 優先度 |
|---|---|---|
| B1 | Pi Zero 2W でWhisper tiny + Bonsai 1.7B + MiniLM の動作確認（メモリ・速度計測） | Early |
| B2 | バッテリーHATの選定・Tier1/2/3の消費電力実測・稼働時間算出 | Mid |
| B3 | Soul DockプロトタイプのPogo Pin設計 + 3Dプリンタ出力 + STLをGitHub公開 | Mid |
| B4 | Coreボードの回路設計（KiCad）・Gerberファイル公開 | Later |
| B5 | Shell設計仕様の策定 + Printablesへの第1世代Shell投稿 | Later |

### Track C — 選択的自己開示の設計

| # | 作業内容 | 優先度 |
|---|---|---|
| C1 | 開示カテゴリの定義と境界ケースの洗い出し | Early |
| C2 | Capability Token仕様の設計（スコープ・期間・粒度の3軸） | Mid |
| C3 | 開示スコープを変えながらClaudeと会話し、違いを体験的に記録 | Mid |
| C4 | 管理UIの設計（スマホアプリ・3機能から始める） | Mid-Late |
| C5 | 他者間共有プロトコルの設計（同意・範囲合意・取り消し） | Later |

---

## 8. 未解決の設計上の問い

| 問い | 状況 |
|---|---|
| マイクの常時起動は法的・倫理的に許容されるか？ | 「VADのみ・録音しない」か「ユーザーが明示的に開始」かを決める必要あり |
| BLE帯域でSOUL差分同期は現実的か？ | 実効1Mbps。差分設計の精度と有線フルマージ頻度で対応予定 |
| デジタル人格としての利用で生じる倫理的問題 | 死後のSOUL・AIが人を「演じる」境界 |
| CoreのOSとソフトウェアのライセンス戦略 | Bonsai/Whisper/MiniLMはOSS。Ghost-Printer自体の公開範囲を要定義 |

---

## 9. 技術スタックまとめ

```
OS:              Raspberry Pi OS Lite (headless)
LLM:             Bonsai 1.7B (1-bit, 240MB, Apache 2.0)
ASR:             Whisper.cpp tiny (39MB, MIT)
Embedding:       all-MiniLM-L6-v2 (22MB, Apache 2.0)
Geocoding:       Nominatim + OSM (offline)
Soul format:     カスタムバイナリ (.soul) + JSON (開発中はJSON)
Sync protocol:   CRDT (Automerge or custom) + BLE GATT
Trust:           ECDH (有線初回) + BLE (差分)
Permission:      Capability Token (JWT互換を検討)
Plugin I/F:      Soul Plugin Spec v1 (設計中)
Hardware:        Raspberry Pi Zero 2W → 専用基板（将来）
Shell:           3Dプリンタ対応 STL / STEP (KiCad for PCB)
```

---

## 10. 参考資料・関連プロジェクト

- **Bonsai 8B (PrismML):** 1ビット量子化LLM。1.7Bモデルが0.24GB。Apache 2.0。https://prismml.com
- **ActivityPub:** フェデレーション型SNSプロトコル。Soul Protocolの設計参照。
- **Framework Laptop:** Core販売 + OSS修理・拡張のビジネスモデル参照。
- **YubiKey / Ledger:** 物理的所持による信頼確立の参照事例。
- **Automerge:** CRDT実装ライブラリ（JavaScript/Rust）。

---

*このドキュメントはGhost-Printerプロジェクトの設計議論（2026-04-08）をCoworkが引き継ぐために作成された。Track Aから着手することを推奨する。*
