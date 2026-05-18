# C6 — Soul Protocol L1-L5 仕様書

**更新日:** 2026-05-04
**ステータス:** Approved (qvp 2026-05-04 承認、 推奨案ベース、 一部 Q4 で調整)
**前提:** memory「ActivityPub の Personal Identity 版」を具体仕様に落としたもの
**関連:** `specs/a6_soul_binary_format.md` (HEADER+APPEND_LOG+暗号化セクション),
`specs/c1_disclosure_spec.md` (8 disclosure category + scope), `capability_token.py`
(C2: Token), `permission_gateway.py` (C4: Gateway)

---

## 0. 設計目的

Ghost-Printer は単独で完結するが、 将来:

1. **同一所有者の複数端末** (例: メイン OPi 3B + ポケット端末) で SOUL を一致
2. **外部 AI (Claude.ai 等)** に部分ビューを渡す
3. **plugin として外部サービス** が SOUL に話しかける

この **「外との関わり」を 5 層に整理した** プロトコル。 現状の C2 Token + C4 Gateway + A6 バイナリは「単独デバイス内」に閉じているので、 これらを **デバイス境界を越えても動く** ように標準化する。

---

## 1. 全体像

```
                          外の世界
   ┌──────────────────────────────────────────────────┐
   │ L5 Plugin     外部 AI / アプリ統合                  │
   ├──────────────────────────────────────────────────┤
   │ L4 Merge      他デバイス SOUL の取込み (CRDT)        │
   ├──────────────────────────────────────────────────┤
   │ L3 Permission Capability Token 配布・検証             │
   ├──────────────────────────────────────────────────┤
   │ L2 Sync       APPEND_LOG エントリ + snapshot 整合性    │
   ├──────────────────────────────────────────────────┤
   │ L1 Discovery  Soul Dock 物理接続 + DID ペアリング    │
   └──────────────────────────────────────────────────┘
                  自分の Ghost-Printer
```

下層が信頼の基盤で、 上層に進むほどアプリ寄り。 Pre-MVP では L1-L4 を実装、 L5 は次フェーズ。

---

## 2. L1 — Discovery (発見と信頼の起点)

### 2.1 デバイス識別子 (Q1 採択)

**内部識別** = **DID** (W3C Decentralized Identifiers, `did:key` method):

```
did:key:z6MkrCD1c8vJxKj6q...
        ^^^
        Multibase prefix (z = base58btc)
        Multicodec (0xed = Ed25519 public key)
```

各デバイスは Ed25519 鍵ペアを生成。 公開鍵が **そのままデバイスの DID** になる
(self-sovereign)。

**人間表示** = **Fediverse 風 handle**:

```
@haru@gp1.local        ローカルだけのデバイス
@haru@ghostprinter.haru.example   公開アドレス
```

UI / ログ / 通信のメタデータでは handle を使い、 内部の認証 / 鍵検証では
DID を使う。 handle → DID の解決はデバイス内ローカル設定で行う (= ActivityPub
の WebFinger を簡略化)。

### 2.2 鍵階層

```
device_master_secret (HKDF root)
   ├─ Ed25519 keypair    → DID, peer 認証
   ├─ A6 master_key       → SOUL section 暗号化 (既実装)
   └─ recovery_seed       → リカバリ用 (Bitcoin BIP-39 風)
```

3 つは同一 master_secret から HKDF 派生で生成される。 master_secret は
デバイス起動時に生成 (or recovery_seed から復元)。

### 2.3 Soul Dock 物理接続によるペアリング (Q2 + Q3 採択)

**Pre-MVP では Soul Dock 物理接続のみ** が信頼の起点。 BLE / Wi-Fi / mDNS は
すべて scope 外。

ペアリング手順:

```
Step 1: 両端末を Soul Dock に物理接続 (Pogo Pin 4 極)
    ↓
Step 2: USB enumeration → デバイス相互認識
    ↓
Step 3: Ed25519 公開鍵を双方向交換
    ↓
Step 4: 信頼ストアに記録 (data/trusted_devices.json or 暗号化セクション 0x0D)
    ↓
Step 5: 以後の接続では公開鍵で認証 (パスワード不要)
```

**信頼ストアの中身:**

```json
[
  {
    "did": "did:key:z6MkrCD1c8vJxKj6q...",
    "handle": "@haru@gp1.local",
    "public_key": "<32B Ed25519 pub>",
    "paired_at": "2026-05-10T22:11:33Z",
    "last_seen_at": "2026-05-11T07:42:11Z",
    "trust_level": "owner"
  }
]
```

`trust_level`:
- `owner`: 同じ master_secret 由来の自分の他端末 (= 全権限)
- `friend`: scope 外 (Pre-MVP では発行不能)
- `guest`: 一時的な部分開示用 (将来)

### 2.4 友達 / 公開アドレッシングは scope 外

Q1 で議論した「他人と SOUL を交換」「公開 fediverse handle」 は **Pre-MVP
では実装しない**。 仕様書 §10 に拡張ポイントとして残すのみ。

---

## 3. L2 — Sync (端末間で SOUL を一致させる)

### 3.1 トランスポート (Q2 採択)

**Pre-MVP は Soul Dock USB のみ**。

| トランスポート | Pre-MVP | 後フェーズ |
|--------------|---------|----------|
| Soul Dock USB 2.0 (480 Mbps) | ✅ メイン | ✅ |
| BLE | ❌ scope 外 | △ A6 暗号化を上乗せして検討 |
| mDNS / Wi-Fi LAN | ❌ scope 外 | △ |
| HTTPS over Internet | ❌ scope 外 | ❌ |

### 3.2 同期トリガ (Q4 採択 = 選択肢 A)

**Soul Dock 充電中に全同期**。 ユーザは普段通り端末を使い、 寝るとき
ドックに挿す → 朝には同期済みで持ち出せる。

```
   Day 1                       Night 1                  Day 2
[ 端末 A 単独動作 ]   ────▶  [ 両端ドックに挿入 ]  ────▶ [ 一致した状態で再開 ]
[ 端末 B 単独動作 ]            ↕ USB 経由で sync
                              merkle 整合性確認
                              不整合なら snapshot 交換
```

不整合が無ければ entry diff のみで秒で完了。 不整合があれば snapshot 全体
交換 (~数 KB-数十 KB) で復旧。

### 3.3 同期粒度 (Q4 採択 = ハイブリッド)

| 状況 | 粒度 |
|------|------|
| 通常 (両端の merkle が祖先関係にある) | **APPEND_LOG エントリ単位** で差分送信 |
| 不整合検出 (merkle が分岐) | **snapshot 全体** を送って再 fold |
| 初回ペアリング | snapshot 全体 (= 完全コピー) |

A6 の APPEND_LOG エントリは既に CBOR 形式・暗号化済み・改ざん耐性あり。
そのまま転送するだけで cryptographic integrity は保たれる。

### 3.4 同期メッセージプロトコル

USB 上に CBOR 形式で次のメッセージをやり取りする (順序は TCP/IP 不要、
USB の reliable transport に任せる):

```cbor
HELLO {
    "msg": "HELLO",
    "from_did": "did:key:z6Mk...",
    "to_did": "did:key:z6Mk...",
    "session_id": <uuid>,
    "snapshot_epoch": <u32>,
    "merkle_root": <bytes 32>,
    "log_entry_count": <u32>,
}

HELLO_ACK { 同上 }

REQUEST_LOG {
    "msg": "REQUEST_LOG",
    "session_id": <uuid>,
    "since_epoch": <u32>,
}

LOG_ENTRIES {
    "msg": "LOG_ENTRIES",
    "session_id": <uuid>,
    "entries": [<encrypted CBOR record>, ...],   // A6 形式そのまま
    "more_available": <bool>,
}

REQUEST_SNAPSHOT {
    "msg": "REQUEST_SNAPSHOT",
    "session_id": <uuid>,
}

SNAPSHOT {
    "msg": "SNAPSHOT",
    "session_id": <uuid>,
    "soul_bin": <bytes>,    // A6 .soul バイナリ
    "size": <u32>,
}

DONE {
    "msg": "DONE",
    "session_id": <uuid>,
    "success": <bool>,
    "applied_count": <u32>,
}

ERROR {
    "msg": "ERROR",
    "session_id": <uuid>,
    "code": <string>,        // "auth_failed" / "epoch_mismatch" / etc
    "detail": <string>,
}
```

### 3.5 同期フロー (典型)

```
端末 A (epoch=42, merkle=X)             端末 B (epoch=39, merkle=Y)
    │                                       │
    │── HELLO {epoch=42, merkle=X} ──▶      │
    │   ◀─ HELLO_ACK {epoch=39, merkle=Y} ──│
    │                                       │
    │   (B の epoch < A の epoch)            │
    │                                       │
    │   ◀─ REQUEST_LOG {since_epoch=39} ────│
    │── LOG_ENTRIES [3 entries] ────▶       │
    │                                       │
    │   (B が apply してから自分の log を返す) │
    │                                       │
    │   ◀─ REQUEST_LOG {since_epoch=42} ────│
    │── LOG_ENTRIES [] ─────────────▶       │
    │                                       │
    │   (両者 epoch=42 で一致、 merkle 再計算)
    │                                       │
    │   ◀─ DONE {success=True} ─────────────│
    │── DONE {success=True} ──────▶         │
    └───────────────────────────────────────┘
```

不整合 (= 共通祖先がない / merkle が分岐) のときは:

```
端末 A: epoch=42, merkle=X    │
端末 B: epoch=42, merkle=Z    │  (同 epoch だが merkle 違う = 分岐)
                               │
                  REQUEST_SNAPSHOT を交換 → 双方の SOUL を decode → CRDT で merge
                  → 新スナップショットを両者で生成 → 新 epoch (max+1) を採番
```

---

## 4. L3 — Permission (Token 配布・検証)

### 4.1 既存 C2/C4 を protocol レベルで標準化

C2 `CapabilityToken` / C4 `PermissionGateway` は既に実装済み。 これを protocol
レイヤで「相手に token を渡す」「相手から提示された token を検証する」 形式化する。

### 4.2 トークン発行・配布フロー

```
[Soul Dock 接続中]
    端末 A (発行側)                          端末 B (受信側)
    │                                       │
    │   ◀── REQUEST_TOKEN {scope_name} ─────│
    │                                       │
    │   TokenManager.issue() で発行          │
    │   関連する section_keys を計算          │
    │                                       │
    │── TOKEN_GRANT {                       │
    │     token: <jwt>,                     │
    │     scope: <DisclosureScope>,         │
    │     section_keys: {sid: key, ...},    │   ← A6 暗号化セクション鍵
    │     expires_at: <iso>,                │
    │   } ──────────────────────────▶       │
    │                                       │
    │   (B が以後 SOUL の partial_view を    │
    │    その scope で読めるようになる)        │
```

`section_keys` は **暗号化 USB 通信内** で渡す。 この channel 自体が
Soul Dock 物理接続なので、 漏洩リスクは「ドックへの物理アクセス」だけ。

### 4.3 同自己 (= owner) 間では全権限が自動付与

ペアリング済みの owner デバイス間では token を毎回発行しない。
ペアリング時に **「永久 owner token」** を発行して保存。 同期時はこれを使う。

### 4.4 Revocation の伝播

- 各 token には `jti` (token id) が付く
- 失効リスト (`revoked_jtis`) は SOUL の `meta` に保存され、 同期される
- 受信側は新たな token を使う前に常に失効リストをチェック
- = eventually consistent。 失効が伝わるまでに最大「次の sync まで」のラグ

---

## 5. L4 — Merge (CRDT による状態統合)

### 5.1 採用方針 (Q5 採択)

**Op-based CRDT を自前実装**。 A6 で既に APPEND_LOG (epoch_counter + ops) の
土台があり、 そのまま CRDT として使える。 Automerge / Yjs 等の依存は入れない。

### 5.2 全順序の決定

各 op に `(device_id, epoch_counter)` のペアが付与される。 デバイス間での
全順序は次の lexicographic order:

```
(epoch_a, device_id_a)  <  (epoch_b, device_id_b)
    iff
   epoch_a < epoch_b
   or (epoch_a == epoch_b and device_id_a < device_id_b)
```

これで「同じ epoch で別デバイスが書いた op」も決定的に並べられる。

### 5.3 op 種別と CRDT 解決

| データ | CRDT 種別 | 解決方法 |
|--------|-----------|----------|
| `core_identity[dim]` の {μ, σ} | LWW (epoch ベース) | 大きい epoch が勝つ |
| `episodic_memory.recent[]` | OR-Set (`ep.id` で dedup) | 集合 union |
| `episodic_memory.compressed[]` | OR-Set | 集合 union |
| `semantic_map.interests[topic]` | G-Counter | カウンタ加算 |
| `semantic_map.values[value]` | G-Counter | カウンタ加算 |
| `temporal_patterns.routines[]` | OR-Set + LWW | id で dedup、 同 id は LWW |
| `watchpoints[].observations[]` | append-only list | 順序付き concat |
| `watchpoints[].state` | LWW | epoch ベース |
| `stats.total_*` | G-Counter | 加算 |

### 5.4 op の commute 性

決定論的 merge を保証するために、 op 適用は **可換** でなければならない:

- `decay_episode` × 2 回適用 → 1 回適用と同じ (冪等性)
- `add_episode A` then `add_episode B` ≡ `add_episode B` then `add_episode A` (可換)
- `update_core_dim X to v1` then `update_core_dim X to v2` → epoch の大きい方が
  勝つ (LWW commute 保証)

### 5.5 「他者から見た私」の取り込み (Q6 採択)

**Pre-MVP では実装しない、 仕様書に方針として記録**。

将来の動作:

```
alice デバイスから haru の SOUL に対して提供される観測:
   {
     "kind": "external_observation",
     "from_did": "did:key:alice...",
     "target_did": "did:key:haru...",
     "personality_signals": [{dim: "extraversion", value: 0.3, confidence: 0.4}],
     "rationale": "alice から見ると haru は最近内向的に見える"
   }

→ haru の SOUL に **弱い prior** として取り込み:
     weight = trust_level(alice) × 0.1   (= 一般的に 0.05-0.15)
     core_identity の baynesian_update を弱い confidence で実行
```

「他人の目も自分の一部」 を「弱め」に取り込む。 完全に混ざりはしないが、
親密な相手の見方は薄く影響する、 という設計。

---

## 6. L5 — Plugin (外部 AI / アプリ統合, Pre-MVP 未実装)

### 6.1 設計の方針 (実装は次フェーズ)

外部 AI (Claude.ai 等) や アプリ (Health Coach, Notion Export 等) が
Ghost-Printer に話しかけるための共通プロトコル。

候補:

| 方式 | メリット | デメリット |
|------|---------|----------|
| **MCP (Model Context Protocol)** | Anthropic 標準、 Claude エコシステム親和性 | 比較的新しい |
| ActivityPub C2S | Fediverse 標準、 federation 親和性 | 重い |
| 独自 JSON-RPC | シンプル | 標準なし |

→ **MCP が有力**。 ただし採択は次フェーズで実機データを見てから決める。

### 6.2 設計原則

- すべての plugin 通信は L3 (Capability Token) を通る
- plugin manifest で要求 scope を宣言
- ユーザが各 plugin に対して個別に token を発行する
- plugin は受け取った partial_view を自分のサーバに保存しない (manifest で同意)

---

## 7. Soul Dock 物理仕様

### 7.1 接続方式

**Pogo Pin 4 極** + USB 2.0:

| ピン | 信号 | 役割 |
|------|------|------|
| 1 | GND | グラウンド |
| 2 | VBUS (5V) | 充電 + 電源 |
| 3 | D+ | USB データ + |
| 4 | D- | USB データ - |

### 7.2 接続検出

- VBUS 立ち上がりエッジ → Soul Dock 検出
- USB enumeration → 相手デバイスの DID 取得
- 既知 DID なら自動 sync 開始
- 未知 DID なら手動承認待ち (UI 経由)

### 7.3 データ + 充電の同時実行

- USB 2.0 (480 Mbps) で SOUL 同期 + 5V/3A で充電を同時
- 同期完了後も充電は継続
- 充電器側からのコマンドで「同期だけ済めば抜いて OK」LED 等で表示

### 7.4 物理筐体 (将来)

- 充電クレードル状の筐体
- 端末を縦置き / 寝かせて置くだけ
- Pogo Pin の接触は数 N の磁石または重力で確保
- ESD 保護 (TVS ダイオード) を Pogo Pin 直下に

---

## 8. リスクと対策

| リスク | 影響 | 対策 |
|--------|------|------|
| ドック未挿入で同期遅延 | 1 日分の差分が翌日まとめて流れる | 機能的には問題なし。 LED で「未同期 N 日」表示 |
| 物理ドックへの第三者侵入 | section keys が漏れる可能性 | ドック自体に物理鍵 / 顔認証 (将来)。 当面は自宅置きで |
| 共通祖先のない CRDT 分岐 | snapshot 全体送信が頻発 | 通常はないが、 端末長期未接続時に発生。 帯域は USB なので問題なし |
| Ed25519 鍵の紛失 | 端末のアイデンティティ消滅 | recovery_seed から復元。 12 単語をユーザに保管させる (Bitcoin BIP-39 風) |
| 失効 token の再利用 | 攻撃者が古い token で侵入 | jti の失効リストを SOUL に保存して同期。 Eventually consistent |
| 「他者から見た私」取込みでアイデンティティが揺らぐ | 自己像の歪み | 取込み係数を 0.1-0.2 に固定、 ユーザが信頼する相手のみ。 Pre-MVP では未実装 |

---

## 9. 採択表

| Q | 採択内容 | 備考 |
|---|---------|------|
| Q1 公開名前 | DID (内部) + Fediverse handle (UI) のハイブリッド | W3C 準拠 + UX 友好 |
| Q2 局所発見 | **Soul Dock 有線のみ** | BLE / mDNS / Wi-Fi は scope 外 |
| Q3 信頼起点 | **Soul Dock 物理接続のみ** | 友達交換は scope 外 |
| Q4 同期粒度 | **entry 単位 + 不整合時 snapshot**、 **ドック充電時に発動** | Wi-Fi 不使用 |
| Q5 CRDT | Op-based 自前実装 (A6 APPEND_LOG と整合) | Automerge 不使用 |
| Q6 他者から見た私 | **弱い prior として取込み (実装は次フェーズ)** | 取込み係数 0.1-0.2 |

---

## 10. 段階的移行

| フェーズ | 状態 | 何が動くか |
|---------|------|----------|
| Pre-MVP (現在) | 仕様確定 (本ドキュメント) | 1 台で完全動作。 sync 未実装 |
| MVP α (実機 1 台) | A6/A7 実装は既に完了 | 本ドキュメントの仕様は spec のみ |
| MVP β (実機 2 台 + Soul Dock) | L1-L4 実装着手 | ペアリング → 同期 → CRDT merge |
| 拡張 | L5 plugin (MCP)、 BLE 拡張、 友達交換、 「他者から見た私」 | 実機データを見てから採択 |

---

## 11. Pre-MVP では実装しないが仕様書に書き残すもの

| 項目 | 理由 |
|------|------|
| BLE トランスポート | 攻撃面増。 A6 上乗せで安全だが、 Pre-MVP の単純さを優先 |
| Wi-Fi / Internet 同期 | 攻撃面増。 ホスト依存性が出る |
| 友達 (= 異 owner) との SOUL 交換 | 信頼チェーン設計が大きい。 Pre-MVP 後で |
| L5 plugin プロトコル | MCP 互換が有力。 実機データを見て採択 |
| 「他者から見た私」の取込み実装 | 哲学的選択は採択済 (b 弱い prior)、 実装は MVP β 後 |
| Recovery seed の UI | BIP-39 風、 ユーザに見せる UX 設計が必要 |

---

## 12. 次のステップ

1. ✅ **本仕様承認** (qvp 2026-05-04)
2. **MVP α まで実装なし** — A6/A7 で構築済みの基盤を実機で検証
3. **MVP β 着手時 (= 実機 2 台 + Soul Dock 試作後)** に L1-L4 を実装:
   - L1: Ed25519 鍵生成 + ペアリング + 信頼ストア
   - L2: USB 上の同期メッセージ実装 (CBOR)
   - L3: 既存 Token を protocol 化
   - L4: CRDT merge エンジン (A6 op を拡張)
4. **拡張フェーズ** (実機で 1-3 ヶ月稼働後) に L5 + BLE + 「他者から見た私」 を採択

---

*本仕様書は「外との関わり」の青写真。 単独デバイスの完成度を優先して、 まずは
A6/A7 を実機で動かし、 その後でこの protocol を実装する。*
