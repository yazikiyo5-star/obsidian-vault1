# A6 — `.soul` バイナリフォーマット仕様書 (draft v0.1)

**更新日:** 2026-05-03
**ステータス:** Approved (qvp 2026-05-03 承認、推奨案で全項採択)
**前提:** ROADMAP §未解決の設計上の問い「.soul バイナリフォーマットの仕様策定」に対する確定仕様 v1
**関連:** `soul_schema.py` (現行JSON), `cortex_manager.py` (GPCXフォーマット), `permission_gateway.py` (部分開示), `watchpoint.py`

---

## 0. 設計目的

現在 `data/soul.json` として保存されている SOUL を、デバイス間配布・部分開示・改ざん検知に耐えるバイナリフォーマット `.soul` に置き換える。CORTEX.bin (GPCX) と一貫性のある「Ghost-Printer ファミリーフォーマット」として整備する。

### 守るべき性質

1. **高頻度書込みに耐える** — 現行 `update_soul()` は1入力ごとに全文書込み。SDカード/eMMC 摩耗対策として **追記ログ + 定期コンパクション** モデルにする
2. **部分読出しが O(1)** — Permission Gateway がスコープ別に `core_identity` だけ・`semantic_map` だけ取り出せるよう、セクション単位で TOC 索引を持つ
3. **セクション単位の暗号化と redaction** — Disclosure Category (8種) ごとに別鍵で封印できる。鍵を渡さなければ復号できない
4. **改ざん検知** — セクションごとの SHA256 を集約した Merkle Root をヘッダに格納
5. **スキーマ進化に強い** — 旧フォーマット(`emotion: {name, intensity}`)と新フォーマット(`emotion_distribution: {joy: 0.3, ...}`) が混在する現実 (`soul.json` 実例参照) を素直に飲み込める
6. **CORTEX.bin との一貫性** — 共通ヘッダ規約 `GP**` 4Bマジック・LE16版数・LE32長さ・SHA256検証 を踏襲する

---

## 1. 全体レイアウト

```
┌─────────────────────────────────────┐  offset 0
│  HEADER (128 B, 固定)                │
├─────────────────────────────────────┤  offset 128
│  TOC (12 B × N entries)             │
├─────────────────────────────────────┤
│  STRING TABLE (任意, dedup用)       │
├─────────────────────────────────────┤
│  SECTION 0x01  CORE_IDENTITY        │
├─────────────────────────────────────┤
│  SECTION 0x02  EPISODIC_RECENT      │
├─────────────────────────────────────┤
│  SECTION 0x03  EPISODIC_COMPRESSED  │
├─────────────────────────────────────┤
│  SECTION 0x04  SEMANTIC_MAP         │
├─────────────────────────────────────┤
│  SECTION 0x05  TEMPORAL_PATTERNS    │
├─────────────────────────────────────┤
│  SECTION 0x06  WATCHPOINTS          │
├─────────────────────────────────────┤
│  SECTION 0x07  STATS                │
├─────────────────────────────────────┤
│  ... 任意の追加セクション           │
├─────────────────────────────────────┤
│  APPEND LOG (option, セクション後)   │
├─────────────────────────────────────┤
│  FOOTER (32 B, Merkle Root再掲)     │
└─────────────────────────────────────┘
```

セクションは「順番に並んでいる」だけで、TOC を見れば任意のセクションへ即ジャンプできる。Permission Gateway は TOC の section_id だけ見て読みたい範囲を `pread()` できる。

---

## 2. HEADER (128 B, 固定)

| offset | size | field            | 内容                                                  |
|--------|------|------------------|-------------------------------------------------------|
| 0      | 4    | `magic`          | ASCII `"GPSL"` (Ghost-Printer SoUL)                   |
| 4      | 2    | `format_version` | uint16 LE。 v1 = 0x0001                               |
| 6      | 2    | `flags`          | bit0=encrypted, bit1=compressed_body, bit2=sealed     |
| 8      | 8    | `created_at_ms`  | int64 LE, Unix ミリ秒                                 |
| 16     | 8    | `updated_at_ms`  | int64 LE                                              |
| 24     | 32   | `owner_id_hash`  | SHA256(owner_secret \|\| salt)。所有者識別 (匿名可能) |
| 56     | 4    | `toc_offset`     | uint32 LE。通常 128 (HEADER直後)                      |
| 60     | 2    | `section_count`  | uint16 LE                                             |
| 62     | 2    | `cortex_link`    | このSOULを生成した CORTEX のメジャー版 (互換性管理用) |
| 64     | 4    | `body_length`    | uint32 LE。 footer 含めない、TOC～最終セクション末    |
| 68     | 4    | `crc32_header`   | HEADER 0..68 の CRC32 (高速整合性チェック)            |
| 72     | 32   | `merkle_root`    | 各セクション SHA256 をリーフとした Merkle Root        |
| 104    | 4    | `epoch_counter`  | uint32 LE。書込みごとに+1。CRDT 同期時のロジカル時計  |
| 108    | 16   | `device_id`      | このデバイス由来の SOUL であることを示す UUIDv4       |
| 124    | 4    | `reserved`       | 0 埋め (将来拡張)                                     |

合計 **128 B**。MEMORY.md の "HEADER(128B)" 規約に整合。

### `flags` ビット詳細

| bit  | 意味                                                              |
|------|-------------------------------------------------------------------|
| 0    | `encrypted` — セクション本体が AES-256-GCM 封印済み (鍵は外部)   |
| 1    | `compressed_body` — セクション本体が zstd で圧縮されている        |
| 2    | `sealed` — このSOULは「公開済み（書換禁止）」スナップショット    |
| 3    | `partial` — 一部セクションが redaction された開示用ビュー         |
| 4–15 | reserved                                                          |

---

## 3. TOC (Table of Contents)

ヘッダの `toc_offset` から `section_count` 個ぶん、固定12Bで並ぶ。

| offset | size | field            | 内容                                              |
|--------|------|------------------|---------------------------------------------------|
| 0      | 1    | `section_id`     | uint8。下表参照                                   |
| 1      | 1    | `encoding`       | uint8。0=raw struct, 1=CBOR, 2=zstd(CBOR), 3=AES-GCM |
| 2      | 4    | `offset`         | uint32 LE。ファイル先頭からのオフセット           |
| 6      | 4    | `length`         | uint32 LE。本体バイト長                           |
| 10     | 2    | `flags`          | bit0=hidden_to_default_scope, bit1=append_log_present, ... |

### セクション ID 一覧

| ID    | 名前                  | エンコーディング推奨 | Disclosure Category 対応 |
|-------|----------------------|----------------------|--------------------------|
| 0x01  | CORE_IDENTITY        | raw struct           | Core Identity            |
| 0x02  | EPISODIC_RECENT      | CBOR (+ zstd)        | Episodic Memory          |
| 0x03  | EPISODIC_COMPRESSED  | CBOR + zstd          | Episodic Memory          |
| 0x04  | SEMANTIC_MAP         | CBOR                 | Interests & Values       |
| 0x05  | TEMPORAL_PATTERNS    | CBOR                 | Behavioral Patterns      |
| 0x06  | WATCHPOINTS          | CBOR                 | Behavioral Patterns      |
| 0x07  | STATS                | raw struct           | (Always disclosable)     |
| 0x08  | EMOTIONAL_STATE      | CBOR                 | Emotional State          |
| 0x09  | HEALTH_VITALS        | CBOR + AES-GCM       | Health Vitals            |
| 0x0A  | LOCATION_TRACE       | CBOR + AES-GCM       | Location & Movement      |
| 0x0B  | SOCIAL_GRAPH         | CBOR + AES-GCM       | Social Graph             |
| 0xFE  | STRING_TABLE         | length-prefixed UTF8 | (内部参照用)             |
| 0xFF  | APPEND_LOG           | length-framed CBOR   | 直近書込みのジャーナル    |

> **設計判断:** Permission Gateway の C1 開示カテゴリ8種と1対1で対応させ、 redaction 時は単にセクションを丸ごと削除すれば良い構造にする。

---

## 4. セクションごとの仕様

### 4.1 SECTION 0x01 CORE_IDENTITY (raw struct, 高速パス)

10次元 × {μ, σ} を **f32 LE で隙間なく** 並べる。可変ではないので CBOR にする旨味なし。

```
struct CoreIdentity {
    f32 dims[10][2];       // [openness, conscientiousness, ..., independence] × [μ, σ]
}                          // 80 B 固定
```

次元順序は `soul_schema.py:create_empty_soul()` の dict 挿入順を正準とする。CORTEX.bin にも同じ順序が定義済みなので参照する。

### 4.2 SECTION 0x02 EPISODIC_RECENT (CBOR + zstd)

エピソードは件数・サイズが大きく揺れる + スキーマも揺れる (旧 `emotion` / 新 `emotion_distribution`) ため、自己記述的な **CBOR 配列** を採用。zstd で平均 0.3 倍に圧縮できる (raw_text が日本語UTF-8で重いため効果大)。

```cbor
[                                              ; CBOR array of episodes
  {
    "id": "ep_20260415_162452",
    "ts_ms": 1745222292593,                    ; uint64 (ISO文字列を解析しなくて済む)
    "raw_text": "今日は朝から…",
    "summary": "…",
    "importance": 0.6,
    "emotion": {"name": "calm", "intensity": 0.6},
    "emotion_distribution": null,
    "personality_signals": [
      {"dim": 0, "value": 0.7, "confidence": 0.7}   ; dimは0..9のインデックス
    ],
    "topic_ids": [12, 7, 33],                  ; STRING_TABLE への参照
    "value_ids": [4, 9],
    "context": {...},
    "weight": 1.0,
    "acoustic": null
  },
  ...
]
```

**設計判断（重要）:**
- ISO8601文字列 → `ts_ms` int64 化でパース不要・固定長化
- `dimension` 文字列 → `dim` インデックス (固定10次元なので)
- `topics` / `values` 配列 → `topic_ids` int配列 + STRING_TABLE で重複排除
- 旧/新フィールド共存 → どちらも optional として CBOR に持たせ、reader が片方ずつ参照

### 4.3 SECTION 0x03 EPISODIC_COMPRESSED (CBOR + zstd)

GMM クラスタの中心 + メンバ件数 + 代表ベクトル。スキーマは将来 A4 拡張で詰める。

### 4.4 SECTION 0x04 SEMANTIC_MAP (CBOR)

```cbor
{
  "interests": [[12, 5], [7, 3], [33, 1]],     ; [topic_id, count]
  "values":    [[4, 2], [9, 1]]                ; [value_id, strength]
}
```

### 4.5 SECTION 0x05 TEMPORAL_PATTERNS (CBOR)

`active_hours`, `routines` (HMM パラメータ)。

### 4.6 SECTION 0x06 WATCHPOINTS (CBOR)

`watchpoint.py` の WatchPoint オブジェクトを CBOR 化。state enum は uint8 化。

### 4.7 SECTION 0x07 STATS (raw struct, 16 B)

```
struct Stats {
    uint32 total_episodes;
    uint32 total_updates;
    uint64 last_evolve_ts_ms;
}
```

### 4.8 SECTION 0xFE STRING_TABLE

```
uint32 entry_count
[
  uint16 length
  uint8  bytes[length]   ; UTF-8
] × entry_count
```

エピソード追加で新しいトピック語彙が現れる頻度はそこそこ高いので、コンパクション時にだけ再構築する。中間は `APPEND_LOG` のローカルテーブルで暫定 ID を割り振る。

### 4.9 APPEND_LOG (`.soul.log` 別ファイル, 2026-05-03 実装済み)

**設計判断:** `.soul` 本体に APPEND_LOG セクションを内包すると、追記のたびに TOC を再書込する必要があり「追記のみ」が成立しない。代わりに **別ファイル `.soul.log`** とし、`.soul` 本体は完全な「コンパクト化済みスナップショット」として扱う。これでログ追記は SD/eMMC 上で純粋な append のみになる。

#### `.soul.log` ファイル形式

```
HEADER (16 B):
  [4 B  magic "GPLG"]
  [2 B  version (LE u16)]
  [2 B  flags (LE u16)] — bit0 = encrypted (per-entry AES-256-GCM)
  [4 B  snapshot_epoch (LE u32) — 直近コンパクション時の epoch_counter]
  [4 B  reserved]

ENTRIES (繰り返し):
  [4 B  body length (LE u32)]
  [N    body — 平文 CBOR、または AES-GCM 暗号化済 CBOR (ciphertext+tag)]
```

#### 暗号化モード (2026-05-03 実装済み)

`SoulLog(path, master_key=master)` で初期化すると:

- **鍵**: `HKDF-SHA256(master_key, info="soul_log:epoch_{snapshot_epoch}")` → 32 B
- **IV (12 B)**: `SHA256("soul_log_iv:" + epoch:LE32 + index:LE32)[:12]` (決定論)
- **AAD**: `"soul_log:" + epoch:LE32 + index:LE32` (位置・log間 swap 防御)
- **不変条件**: 同一 epoch 内では index が単調増加 (= IV 一意)。 `clear()` は必ず epoch を進める (`compact_with_log` / `ShadowStorage` がこれを保証)。
- **鍵の更新**: snapshot_epoch ごとに HKDF info が変わるので、コンパクション後の log は別鍵で封印される (forward secrecy 風)。

**privacy 保証**: master_key を持たない攻撃者は raw_text を含むエントリを復元できない。 ファイルの ASCII 検査でも `raw_text` 文字列は出てこない (テストで verification 済み)。

末尾の length-prefix が中途半端なバイト数で書かれていた場合 (電源断など) は **そのエントリだけ捨てて** 安全側に倒す。 `iter_records()` で破損境界を検出。 fsync ありの append しか発生しないので、ほとんどのケースで「直前 1 件まで」が確実に永続化される。

#### Op codes (CBOR record の `"op"` フィールド)

| op_code | 名前                  | 用途                                                    |
|---------|-----------------------|---------------------------------------------------------|
| 1       | `OP_INPUT`            | 入力 replay (raw_text + delta + context)。**主用途**     |
| 2       | `OP_CORE_UPDATE`      | core_identity の dim を直接更新 (デバッグ用)             |
| 3       | `OP_EPISODE_ADD`      | extractor を介さずエピソード追加                          |
| 4       | `OP_DECAY`            | 重み減衰 1 サイクル                                      |
| 5       | `OP_DISTILL`          | 特定エピソードを distill                                  |
| 6       | `OP_WATCHPOINT`       | WP 関連オペ                                              |
| 7       | `OP_STRING_TABLE_EXT` | 文字列テーブル拡張                                       |

主用途は `OP_INPUT` (= extractor の出力 + raw_text を丸ごと残す)。コンパクション時に `update_soul()` に再供給して状態を再構築するため、決定論的に再現可能で CRDT 同期にも使える。

#### コンパクション (Q5 採択ポリシー)

毎日 02:00 + ログサイズ 64 KB 超で即時 (`should_compact()`)。

実装は `soul_log.compact_with_log(snapshot_path, log, replay_fn)`:

1. snapshot を `decode_soul()` で読込 (鍵があれば暗号化セクションも復号)
2. ログを `iter_records()` で順番に流して `replay_fn(soul, record)` を適用
3. epoch_counter を replay 件数だけ進めて `encode_soul()` で再エンコード
4. `.soul.tmp` に書込み → `os.replace()` でアトミック交換
5. ログを `clear(snapshot_epoch=new_epoch)` で空に戻す

**replay_fn は呼出側が渡す** ため、 `soul_log` から `soul_engine` への直接依存を切り離している (HTTPx 等の外部依存をテストに引きずり込まない設計)。

#### 採用パターン (推奨)

```python
from soul_log import SoulLog, append_input_record, compact_with_log, should_compact

log = SoulLog("data/soul.log")

# 1 update ごと:
delta = extractor.extract(raw_text)
soul = update_soul(soul, delta, raw_text, context)         # 既存 in-memory 更新
append_input_record(log,                                    # disk へは追記のみ
                    raw_text=raw_text, delta=delta,
                    context=context, epoch=soul_epoch + 1)

# 定期 (cron 02:00 / または size 超え時):
ok, reason = should_compact(log, last_compact_iso=last_iso)
if ok:
    compact_with_log("data/soul.bin", log, replay_fn=replay_input)
```

**SD/eMMC 寿命の観点:** 1 update につき log への追記 (数百 B) のみ。スナップショット書換は 1 日 1 回 + サイズ閾値 (= 数 KB の 1 度書込)。 HBE ・ eMMC の SLC モード境界 (典型 100 K 〜 1 M write/cell) を考えると、 寿命 1 万年オーダー。

#### 実測 (15 テスト全通過)

| 項目                                         | 結果         |
|----------------------------------------------|--------------|
| append round-trip (記録 → 読出)              | ✅           |
| プロセスまたぎ永続化                         | ✅           |
| 中途切断エントリの安全な破棄                 | ✅           |
| 不正 magic の拒否                            | ✅           |
| compact によるスナップショット反映           | ✅           |
| アトミック交換 (.tmp の事後削除)             | ✅           |
| 既存スナップショット state の保持            | ✅           |
| snapshot 不在時の initial_soul 起動          | ✅           |
| 暗号化付き compact (master_key を貫通)        | ✅           |
| size_threshold での即時トリガ判定           | ✅           |
| append_input_record ヘルパ                   | ✅           |

---

## 5. シリアライズ方式の比較

| 方式                | サイズ | 速度 | スキーマ進化 | ゼロコピ | Pi/RK3566 ライブラリ | 採否 |
|---------------------|--------|------|-------------|----------|----------------------|------|
| 全 raw packed struct | ◎     | ◎   | ✕（バイナリ互換が壊れる） | ◎ | 自作のみ | 部分採用 (固定セクションだけ) |
| CBOR                 | ○     | ○   | ◎           | △ (cbor2) | Python `cbor2`, C `tinycbor`, Rust `ciborium` | **本体採用** |
| MessagePack          | ○     | ○   | ◎           | △ | 多数 | CBORと同等、IETF標準のCBORを優先 |
| Protocol Buffers     | ◎     | ◎   | ◎ (.proto必要) | ✕ | 重い、.proto生成パイプライン必須 | 却下 (運用コスト高) |
| FlatBuffers / Cap'n Proto | ○ | ◎ | ○           | ◎       | Rustあり、Pythonは弱い | 却下 (Python主体に合わない) |
| 全 JSON + gzip (現行 CORTEX流) | △ | △ | ◎ | ✕ | ◎ | 却下 (部分読出し不可、暗号化単位が粗い) |

**選定:** 「**固定形 = raw struct, 可変形 = CBOR (+ zstd 圧縮 / + AES-GCM 封印)**」のハイブリッド。
理由:
- CORE_IDENTITY と STATS は形が確定しており、頻繁にアクセスされるので raw struct で 0 オーバーヘッド
- EPISODIC は形が揺れているので CBOR、サイズも raw_text 由来で重いので zstd
- 暗号化は section 単位で AES-GCM（鍵は外部 = Capability Token と紐付け）
- フォーマット全体としては「レイアウト: 自前 / セクション中身: CBOR」という設計で、PROTO/Schema 生成不要

---

## 6. スキーマ進化戦略

| 進化パターン               | 対応                                                       |
|----------------------------|------------------------------------------------------------|
| 既存フィールドの値域拡張   | format_version は据え置き。CBORなのでそのまま読める        |
| 新フィールド追加 (任意)    | format_version 据え置き。reader 側で missing を許容        |
| 新フィールド追加 (必須)    | format_version の minor を上げる (LE16 の下位8bit)         |
| 既存フィールドの意味変更   | format_version の major を上げる + migration コードを併走  |
| 新セクション ID 追加       | format_version 据え置き。未知IDは reader が無視            |
| 古いセクションを廃止       | major bump + 旧IDを reserved にして二度と使わない          |

`emotion` (旧) と `emotion_distribution` (新) のように **同じ意味で形だけ違う** 場合は、reader で先に新フィールドを見て、無ければ旧を見るフォールバックを実装。新規 episode は新形式のみ書く。

---

## 7. 暗号化と選択的開示の連携 (2026-05-03 実装済み)

### 7.1 鍵階層 (実装済)

```
master_key (デバイス内、HKDF root, 32B 以上)
   ├─ section_key[CORE_IDENTITY]      = HKDF-SHA256(master_key, info="soul:0x01")  [32B]
   ├─ section_key[EPISODIC_RECENT]    = HKDF-SHA256(master_key, info="soul:0x02")  [32B]
   ├─ section_key[HEALTH_VITALS]      = HKDF-SHA256(master_key, info="soul:0x09")  [32B]
   └─ ...
```

各暗号化セクションを **AES-256-GCM** で封印。
- IV (12B) = `SHA256(epoch_counter:LE32 ‖ section_id:U8)[0:12]` (決定論)
- AAD = `section_id:U8 ‖ epoch_counter:LE32` (cross-section swap 防御)
- 暗号文の中身 = `[inner_encoding:1B][元の payload]`。復号後に元の encoding (raw / cbor / cbor+zstd) を復元できる。
- 既定で暗号化されるセクション: EPISODIC_RECENT / EPISODIC_COMPRESSED / EMOTIONAL_STATE / HEALTH_VITALS / LOCATION / SOCIAL

### 7.2 Permission Gateway との接続 (実装済)

```python
from soul_binary import derive_keys_for_token, decode_soul

# Permission Gateway 内部
keys = derive_keys_for_token(master_key, capability_token)
# → token.scope.categories で HIDDEN でないものだけ鍵が出る
view = decode_soul(soul_bytes, section_keys=keys)
# → 鍵が無いセクションは _meta.redacted_sections に section_id が並び、
#   対応する top-level field は最初から存在しない (本物の暗号学的隠蔽)
```

`HIDDEN` カテゴリの鍵は決して導出されないので、トークン発行者（自分）以外は **暗号学的に開示できない**。アプリ層のフィルタ実装ミスがあっても秘密が漏れない。

### 7.3 開示用ビュー — 2 種類のフォーマット

#### A. 平文 partial view (`PermissionGateway.create_partial_view`)

```python
view_bytes, fr = gateway.create_partial_view(soul_bytes, token, master_key)
# → ALWAYS_DISCLOSED + granted セクションのみ、内容は decode→re-encode で平文化
#   受信者は master_key 不要で読める
```

軽量・読みやすさ優先。ただしバイト列が再エンコードで変わるため、原本との
Merkle 結びつきは保持されない。

#### B. 検証可能 partial view (`PermissionGateway.create_verifiable_partial_view`, 2026-05-03 実装)

```python
view_bytes, section_keys, fr = gateway.create_verifiable_partial_view(
    soul_bytes, token, master_key
)
# → 元の section bytes verbatim + SEC_MERKLE_PROOF (0x0C) を同梱
#   受信者は section_keys で復号 + verify_partial_view で原本 root への到達を検証
```

**検証フロー:**

```python
# 受信者側
result = verify_partial_view(view_bytes, expected_original_root=trusted_root)
if result["valid"]:
    decoded = decode_soul(view_bytes, section_keys=section_keys)
    # decoded を信頼して使う
```

**SEC_MERKLE_PROOF (0x0C) の構造 (CBOR):**

```cbor
{
  "original_root": <bytes(32)>,    ; 原本 SOUL の Merkle root
  "total_leaves": <uint>,          ; 原本のセクション数
  "proofs": [
    {
      "sid": <section_id>,
      "leaf_index": <uint>,        ; 原本でのソート後位置
      "siblings": [<bytes(32)>, …] ; 原本 Merkle Tree 上の上りパス兄弟
    },
    …
  ]
}
```

**安全性プロパティ:**

- 元 section bytes が verbatim なので、leaf hash = SHA256(section_body) が原本と一致する
- `MerkleTree.verify(leaf_hash, leaf_index, siblings, original_root)` で
  原本 root への到達を確認できる
- 改ざんされた section / 異なる SOUL から作った view / 偽の proof bundle は
  どれも root mismatch で検出される
- epoch_counter を原本から保持するため、受信者は section_keys (= HKDF(master, "soul:0xNN"))
  で AES-GCM 復号できる (IV = SHA256(epoch ‖ sid)[:12] を再現可能)

**実測サイズ:** N≦8 セクションの SOUL で proof bundle は ~300 B 以内 (1 proof = 32B × log2(N) + メタ)。
N=100 でも 1 KB 以内に収まる (log2(100) ≈ 7 なので siblings 数も少ない)。

**実装済テスト 21/21:**
- MerkleTree 単体 (empty/single/two/three/ten leaves, wrong-leaf/index/root)
- extract_partial_view: バイト保持・partial flag・epoch 保持・除外確認
- verify_partial_view: 正常検証・既知 root マッチ・偽 root 拒否・改ざん検知・proof 不在検出
- E2E: Gateway 経由 round-trip、信頼 root 検証、攻撃者の偽 view が拒否、サイズオーバーヘッド検証

### 7.4 実測オーバーヘッド

| 項目                 | 計測値                          |
|----------------------|---------------------------------|
| ストレージ           | +17 B / encrypted section (16B GCM tag + 1B inner_enc) |
| encode 速度         | +1〜10% (100 ep で 589 → 598 µs) |
| decode 速度 (鍵あり) | -5% 〜 +5% (誤差範囲内)         |
| decode 速度 (鍵なし=redacted) | **大幅高速化** (鍵が無いセクションは復号せず読み飛ばし、100 ep で 459 → 32 µs) |

---

## 8. CRDT / 差分同期との関係

ROADMAP §未解決 「Core間CRDT同期」に向けた前提:

- `epoch_counter` は CRDT のロジカル時計として機能。 `device_id || epoch` がイベントID
- APPEND_LOG の各 delta record はそのまま CRDT operation として配信可能
- 同期プロトコル (Soul Protocol L2 Sync) は「最後に同期した epoch」以降の APPEND_LOG エントリだけ送る
- マージ時は `(device_id, epoch)` 順で全 op を merge → 全 reader が同じ最終状態に収束 (op-based CRDT)
- BLE 1Mbps でも、1 episode 追加 ≒ 数 KB なので問題なし

---

## 9. JSON ⇔ Binary 互換性 (移行戦略)

3段階で進める:

**Stage 1 (本仕様策定後すぐ):** `soul_binary.py` を実装し `soul.json ⇄ soul.bin` の双方向変換器を作る。既存パイプラインは引き続き JSON で動かし、保存時に同じ内容を `.bin` にも書く (シャドー書込)。

**Stage 2 (バイナリ検証後):** Reader を `.bin` 優先に切り替え。書込みも APPEND_LOG モードに移行。`.json` は debug export 専用。

**Stage 3 (デバイス常駐):** SDカード上は `.bin` のみ。`cortex_manager.export_json()` 同等の `soul_binary.export_json()` で人間可読ダンプを生成可能にする。

---

## 10. ファイルサイズ・速度実測 (2026-05-03 prototype)

`soul_binary.py` v0.1 を Mac 上で実装し、合成データ + 実 `data/soul.json` で計測した。

### サイズ (JSON 比)

| 入力                                | JSON     | Binary (.soul) | 比率   |
|-------------------------------------|----------|----------------|--------|
| 実データ `data/soul.json` (4 ep)    | 7,592 B  | 2,167 B        | 28.5%  |
| 合成 4 episodes (重複文字列多)      | 3,164 B  | 862 B          | 27.2%  |
| 合成 30 episodes                    | 18,205 B | 1,413 B        | **7.8%** |
| 合成 100 episodes                   | 58,740 B | 2,737 B        | **4.7%** |

**事前見積もり (50%) より大幅に縮小。** 主因は STRING_TABLE による topics/values の完全 dedup と zstd の効きで、エピソードが増えるほど劇的に縮む。

### 速度 (Mac M-class, 参考値)

| 操作                                     | 4 ep   | 30 ep   | 100 ep  |
|------------------------------------------|--------|---------|---------|
| `encode_soul` (binary)                   | 43 µs  | 176 µs  | 574 µs  |
| `json.dumps + utf-8`                     | 18 µs  | 89 µs   | 288 µs  |
| `decode_soul` (binary, full + Merkle検証)| 33 µs  | 135 µs  | 424 µs  |
| `json.loads`                             | 10 µs  | 53 µs   | 176 µs  |
| `read_section CORE_IDENTITY` のみ        | **0.3 µs** | 0.3 µs | 0.3 µs |

JSON 比で encode/decode は約 2 倍遅いが、絶対値で 100 episodes/<1ms。RK3566 (1.8GHz Cortex-A55 ×4) に降ろしても余裕。

**特筆すべきは部分読み出し速度**: TOC ジャンプ＋80B読出のみなので、JSON parse 全体に対し **数百倍速い**。Permission Gateway が「Health Coach スコープなので CORE_IDENTITY だけ寄こせ」を 1 µs 以下で返せる。

### 帯域 (BLE 1Mbps = 125 KB/s 換算)

| シナリオ                          | サイズ  | 転送時間 |
|-----------------------------------|---------|----------|
| SOUL フルダンプ (100 ep, 暗号化込み)| ~3 KB   | 24 ms    |
| 1 episode 差分 (CBOR delta)        | ~300 B  | 2.4 ms   |
| Soul Protocol L2 Sync (delta束)    | ~1-2 KB | 8-16 ms  |

差分同期は実用域。フルダンプもボタン押下から即時応答に間に合う。

---

## 11. 採択決定 (2026-05-03 qvp 承認)

すべて推奨案で採択。

| # | 項目                              | 採択                                              | 採択理由                                                       |
|---|-----------------------------------|---------------------------------------------------|----------------------------------------------------------------|
| Q1 | エンディアン                      | **Little-Endian**                                 | RK3566/x86 ネイティブ。デバイス内処理優先                      |
| Q2 | 圧縮アルゴリズム                  | **zstd** (level 10, CORTEXは gzip のまま)         | エピソード増加で効くため。reader は encoding バイトで分岐      |
| Q3 | 暗号化スキーム                    | **AES-256-GCM** (セクション単位、IV=SHA256(epoch‖sid)[0:12]) | RK3566 ARMv8 Crypto Extensions でハード加速可                |
| Q4 | `owner_id_hash` のソース          | **デバイス自動生成 + 任意リカバリーシード**        | 普段は匿名・本人意思でのみ復元可能 (Bitcoin seed phrase 方式)  |
| Q5 | APPEND_LOG コンパクション頻度     | **ハイブリッド: 毎日 02:00 + ログ 64KB 超で即時** | アイドル時最優先、突発負荷にも対応                             |
| Q6 | raw_text の扱い                   | **暗号化セクションへ隔離 + 30 日 TTL で自動消去**  | DECAY_HALF_LIFE_DAYS=30 と整合。再抽出窓を残しつつ永続化を回避 |
| Q7 | format_version ビット配分         | **major 5bit / minor 11bit**                      | マイナー追加の余地を多く確保                                   |
| Q8 | バイナリ拡張子                    | **`.soul`**                                       | 短い、語彙とも整合。マジックバイト `GPSL` で衝突回避           |

---

## 12. 次のステップ

1. **本仕様レビュー** — qvp が §11 の Q1〜Q8 を回答 (or 第一案で進めて良いか確認)
2. **`soul_binary.py` プロトタイプ実装** — encoder/decoder/JSON-bridge を Mac 上で実装 (SD不要)
3. **`test_soul_binary.py` 追加** — 既存 `data/soul.json` をラウンドトリップし内容一致を確認、`flags.encrypted` のセクションを別鍵で読めないことを検証、APPEND_LOG コンパクションが冪等なことを検証
4. **ベンチマーク** — JSON版 vs バイナリ版でサイズ・I/O時間・部分読出し時間を計測
5. **Permission Gateway 連携** — Section 鍵 ⇄ Capability Token の紐付け実装

---

## 付録 A: 参考にした既存資料

- `cortex_manager.py` の GPCX フォーマット (4Bマジック + LE16版数 + LE32長 + gzip(JSON)) — 本仕様のヘッダ規約はこの拡張版
- `soul_schema.py:create_empty_soul()` — 4層構造の正準定義
- `soul.json` 実例 — `emotion` vs `emotion_distribution` のスキーマ揺れを直接観察
- `permission_gateway.py` + `specs/c1_disclosure_spec.md` — 8 disclosure category × 4 粒度 の対応設計
- `watchpoint.py` — WP 生態系状態の永続化要件
- ROADMAP §4「実機後に残るその先の課題」— CRDT/BLE同期の前提
