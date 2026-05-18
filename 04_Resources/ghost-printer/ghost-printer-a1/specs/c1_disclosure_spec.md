# Ghost-Printer C1 — 選択的自己開示システム仕様

**ステータス:** Track C1 — Early Priority
**作成日:** 2026-04-15
**目的:** ユーザーが「誰に・何を・どこまで」見せるかを完全に制御するシステムの設計

---

## 1. 概論

### 1.1 背景・問題設定

Ghost-Printerは、ユーザーの個人データ（SOUL）をAIに提供することで、「毎回初対面」から「長年の付き合い」への関係に変える。しかし同時に、最も機微なデータ（健康、位置情報、人間関係）を複数のAI/アプリに与えることになる。

**課題:**
- 「Claude」には開示するが「Work Assistant」には開示しない、という選択が必要
- 同じ「エピソード」がカテゴリ間で重複（会話 = 社会関係 + エピソード記憶）
- 時間とともに分類が変わる（「昔の秘密」と「今の秘密」）
- データの推測可能性が高い情報ほど慎重に

### 1.2 設計原則

**P1: ユーザーが完全に制御**
- AIやアプリが勝手にデータを読まない
- スコープは明示的・変更可能・期間制限付き

**P2: 3軸制御**
- **カテゴリ**: 何を (CORE_IDENTITY, EPISODIC_MEMORY, etc.)
- **粒度**: どこまで (Full, Summary, Anonymized, Hidden)
- **期間**: どの期間 (直近N日、無制限、etc.)

**P3: 境界ケースを明示的に定義**
- 重複するデータは「最も制限的なルール」を適用
- 例: 「会話から推測される健康状態」は HEALTH_VITALS 扱い

**P4: 簡潔な権限モデル**
- Capability Token で複数AIを一元管理
- JWT互換で将来の標準化に対応

---

## 2. 開示カテゴリ（8カテゴリ）

SOULの全情報を8つのカテゴリに分類。各カテゴリは独立した開示制御が可能。

### 2.1 カテゴリ定義と例

| # | カテゴリID | 日本語名 | 説明 | 例 |
|---|---|---|---|---|
| 1 | `core_identity` | コアアイデンティティ | 安定した性格・価値観・信念体系 | 外向性（0.35）、創造性（0.78） |
| 2 | `episodic_memory` | エピソード記憶 | 最近の出来事・会話・イベント | 「カフェで一人コーヒー」「友人Aとディナー」 |
| 3 | `emotional_state` | 感情状態 | 現在・最近の気分・情動 | 「今日は穏やか」「先週は不安が高かった」 |
| 4 | `behavioral_patterns` | 行動パターン | 習慣・日常のリズム・周期性 | 睡眠時間、運動頻度、コーヒー摂取パターン |
| 5 | `health_vitals` | バイタル・健康 | 生体信号・健康指標（将来センサー） | 心拍数、睡眠品質、ストレスレベル |
| 6 | `location_movement` | 位置・移動 | GPS・立地履歴・行動地点 | 「毎朝駅Aを利用」「月1回は渋谷」 |
| 7 | `social_graph` | 社会関係グラフ | 人間関係・つながり・関係強度 | Alice（親友、毎週）、Bob（同僚、月1） |
| 8 | `interests_values` | 興味・価値観 | Semantic Map — 関心分布 | 「テクノロジー（0.9）」「音楽（0.7）」 |

### 2.2 カテゴリ間の関係性・重複

複数カテゴリに属するデータが存在する場合、**最も制限的なカテゴリのルールを適用する**。

```
例1: 「Aさんとのカフェでの会話」
    ├─ episodic_memory（会話内容）
    └─ social_graph（Aさんとの関係）
    → social_graph の制限がより厳しければ、その方を適用

例2: 「仕事の打ち合わせで疲れた」
    ├─ episodic_memory（仕事内容）
    ├─ emotional_state（疲労感）
    └─ health_vitals（心身への影響）
    → health_vitals が HIDDEN なら、この情報全体を非開示

例3: 「毎日6時に起床→ジム→出勤」
    ├─ behavioral_patterns（起床・運動・通勤ルーティン）
    └─ location_movement（出勤地点）
    → location_movement の制限を適用
```

---

## 3. 粒度レベル（Granularity）

各カテゴリのデータは、4段階の粒度レベルで開示する。

### 3.1 粒度レベル定義

| レベル | ID | 説明 | 個人識別性 | 用途 |
|---|---|---|---|---|
| **Full** | `full` | 圧縮・加工なしの完全データ | 最高 | 信頼できる長期パートナー（Claude等） |
| **Summary** | `summary` | 集計・平均化・圧縮版 | 中 | 機能特化型（Health Coach等） |
| **Anonymized** | `anonymized` | 個人識別子削除・ハッシュ化 | 低 | サードパーティアプリ |
| **Hidden** | `hidden` | データ非開示 | 無 | アクセス不可 |

### 3.2 各レベルの具体例

#### 例1: episodic_memory

```
Raw (Full):
{
  "date": "2026-04-10",
  "time": "14:30",
  "location": {"lat": 35.6756, "lon": 139.7670, "name": "Starbucks Shibuya"},
  "people": [{"name": "Alice", "relation": "close friend"}],
  "activity": "Had coffee and discussed new project",
  "emotion": {"valence": 0.8, "arousal": 0.6},
  "importance": 0.7
}

Summary:
{
  "date_week": "2026-04-08 to 2026-04-14",
  "summary": "Had 3 social meetings this week, mostly positive mood",
  "average_importance": 0.65
}

Anonymized:
{
  "date_week": "2026-04-08 to 2026-04-14",
  "summary": "Had 3 social meetings",
  "people_count": 3,
  "location_categories": ["cafe", "park"]
  # 名前・具体位置なし、カテゴリのみ
}

Hidden:
# データなし（"この情報は非開示です"というメッセージのみ）
```

#### 例2: behavioral_patterns

```
Full:
{
  "daily_sleep": [7.5, 7.2, 8.1, 6.8, 7.4, 7.6, 8.0],
  "sleep_start_time": ["23:45", "00:10", "23:30", ...],
  "exercise_days": [1, 0, 1, 0, 1, 1, 0],  # 過去7日
  "coffee_count": [1, 1, 2, 1, 0, 1, 1],
}

Summary:
{
  "avg_sleep_hours": 7.5,
  "sleep_regularity": 0.85,  # 標準偏差の逆数
  "exercise_frequency": "3-4x per week",
  "caffeine_habit": "light to moderate"
}

Anonymized:
# behavioral_patterns は通常、個人識別子がないため Summary と同じ
# または時間帯の分布のみ（具体数値削除）
{
  "sleep_pattern": "regular",
  "activity_level": "moderate"
}

Hidden:
# 非開示
```

#### 例3: social_graph

```
Full:
{
  "relationships": [
    {"name": "Alice", "relation": "friend", "frequency": "weekly", "strength": 0.95},
    {"name": "Bob", "relation": "colleague", "frequency": "daily", "strength": 0.6},
    {"name": "Charlie", "relation": "family", "frequency": "monthly", "strength": 0.8}
  ]
}

Summary:
{
  "relationships_count": 3,
  "strength_distribution": {"strong": 2, "moderate": 1},
  "frequency_avg": "weekly"
}

Anonymized:
{
  "relationships": [
    {"person_id": "hash_abc123", "relation_type": "close", "frequency": "weekly"},
    {"person_id": "hash_def456", "relation_type": "colleague", "frequency": "daily"},
    {"person_id": "hash_ghi789", "relation_type": "family", "frequency": "monthly"}
  ]
  # 名前なし、ハッシュ化されたID、関係型のみ
}

Hidden:
# 完全非開示
```

---

## 4. スコープテンプレート

ユーザーが頻繁に使う開示パターンを「テンプレート」として用意。テンプレートはカスタマイズ可能。

### 4.1 テンプレート一覧

#### T1: Claude Personal
**対象:** Claude（Anthropic）- パーソナルアシスタント用
**方針:** 長期的な関係で、信頼できるため Full 開示多め。ただし位置情報・健康は非開示。

| カテゴリ | 粒度 | 期間制限 | 理由 |
|---|---|---|---|
| core_identity | Full | 無制限 | 性格理解に必須 |
| episodic_memory | Full | 直近90日 | 最近の文脈を保持 |
| emotional_state | Full | 直近7日 | 現在の気分を反映 |
| behavioral_patterns | Summary | 無制限 | 習慣パターン参照用 |
| interests_values | Full | 無制限 | 会話の質向上 |
| health_vitals | Hidden | — | プライバシー優先 |
| location_movement | Hidden | — | プライバシー優先 |
| social_graph | Anonymized | 直近30日 | 人間関係は保持しつつ匿名化 |

**システムプロンプト例:**
```
あなたは {name} のパーソナルアシスタントです。
以下は過去90日間の {name} のSOULデータです：

【性格（安定）】
{core_identity_full}

【最近の出来事】
{episodic_memory_full_90days}

【現在の気分】
{emotional_state_full_7days}

【行動パターン】
{behavioral_patterns_summary}

【関心分野】
{interests_values_full}

位置情報・健康データ・具体的な人名は含まれていません。
```

---

#### T2: Work Assistant
**対象:** 仕事効率化AI（e.g., PolyAI, Slack bot）
**方針:** 仕事関連のみ。私生活・感情は最小限。

| カテゴリ | 粒度 | 期間制限 | 理由 |
|---|---|---|---|
| core_identity | Summary | 無制限 | 仕事スタイルのみ |
| episodic_memory | Summary | 直近30日 | 仕事エピソード（プライベート除外） |
| emotional_state | Hidden | — | プライベート感情非開示 |
| behavioral_patterns | Summary | 無制限 | 仕事時間帯のみ抽出 |
| interests_values | Summary | 無制限 | 仕事関連興味のみ |
| health_vitals | Hidden | — | 非開示 |
| location_movement | Hidden | — | 非開示 |
| social_graph | Hidden | — | 非開示 |

**フィルター例:**
episodic_memory の "仕事関連" のみを抽出（e.g., "Had meeting with team X", "Finished project Y"）。個人的な会話（"Had coffee with Alice"）は要素的に除外。

---

#### T3: Health Coach
**対象:** 健康管理app（e.g., Apple Health 連携アプリ）
**方針:** バイタル・感情・行動パターンに集中。社会関係・位置情報は非開示。

| カテゴリ | 粒度 | 期間制限 | 理由 |
|---|---|---|---|
| core_identity | Summary | 無制限 | ストレス耐性など参考 |
| episodic_memory | Hidden | — | イベント詳細不要 |
| emotional_state | Full | 直近30日 | メンタルヘルス観察 |
| behavioral_patterns | Full | 無制限 | 生活習慣パターン |
| health_vitals | Full | 無制限 | 重要 |
| interests_values | Summary | 無制限 | 運動・食事関連のみ |
| location_movement | Hidden | — | 非開示 |
| social_graph | Hidden | — | 非開示 |

---

#### T4: Minimal
**対象:** 初回接触のAI・信頼未構築
**方針:** 最小限の開示。コアアイデンティティの要約のみ。

| カテゴリ | 粒度 | 期間制限 |
|---|---|---|
| core_identity | Summary | 無制限 |
| その他 | Hidden | — |

---

#### T5: Emergency
**対象:** 緊急用一時スコープ（health crisis時など）
**方針:** すべてを Full 開示。**自動期限付き（1時間）**。

| カテゴリ | 粒度 | 期間制限 | 失効 |
|---|---|---|---|
| 全カテゴリ | Full | 無制限 | 1時間後自動失効 |

使用例：「今、医療AIと緊急相談したい」→ Emergency scope を一時発行 → 相談完了で失効

---

### 4.2 カスタムスコープの作成

テンプレートに加えて、ユーザーが任意にカスタムスコープを定義可能。

```python
# UI/API 例
scope = create_custom_scope(
    name="My Research Partner",
    categories={
        "core_identity": ("full", None),
        "episodic_memory": ("summary", 60),  # 2ヶ月
        "emotional_state": ("anonymized", 30),
        # その他
    },
    expires_at=datetime(2026, 12, 31),  # 失効日時
)
```

---

## 5. 境界ケース定義

複数カテゴリに属するデータをどう扱うか、明示的に定義。

### 5.1 7つの境界ケース

#### BC1: 仕事の会話から推測される健康状態

**シナリオ:**
「今日の午後は疲れて会議を休んだ」

**複合要素:**
- episodic_memory: 仕事の出来事
- emotional_state: 疲労感
- health_vitals: 推測される体調低下

**判定ルール:**
→ **HEALTH_VITALS を主とする**

**理由:**
健康状態に関わるデータは最も機微。たとえ仕事という文脈があっても、推測可能な健康情報は HEALTH_VITALS のルールを適用すべき。

**適用例:**
- Work Assistant は `health_vitals=HIDDEN` なので、この情報全体をフィルタリング
- Claude Personal は `health_vitals=HIDDEN` なので、「疲れた」という内容を除外して「会議を休んだ」のみ開示

---

#### BC2: Aさんとの会話

**シナリオ:**
「昨日カフェでAliceと話した」

**複合要素:**
- episodic_memory: 会話内容・出来事
- social_graph: Alice との関係情報

**判定ルール:**
→ **SOCIAL_GRAPH を主とする**

**理由:**
個人を特定する情報（名前："Alice"）が含まれるため、最も制限的な social_graph のルールを優先。

**適用例:**
- `social_graph=HIDDEN`: この会話全体を非開示
- `social_graph=ANONYMIZED`: "person_hash_xyz123" との会話内容は開示可（人名ハッシュ化）
- `social_graph=FULL`: 「Alice との会話」完全開示

---

#### BC3: 場所から推測される行動パターン

**シナリオ:**
「毎日朝6時に渋谷駅に着く」

**複合要素:**
- behavioral_patterns: 睡眠・移動・朝のルーティン
- location_movement: 具体的な位置情報

**判定ルール:**
→ **LOCATION_MOVEMENT を主とする**

**理由:**
位置情報は最も個人識別性が高い。場所データなしにはパターンも特定困難なため、より制限的な location_movement のルールを適用。

**適用例:**
- `location_movement=HIDDEN`: この情報全体を非開示
- `location_movement=ANONYMIZED`: 「毎朝ある駅（location_hash_abc）に着く」のみ
- `location_movement=FULL` + `behavioral_patterns=SUMMARY`: 両方開示（場所 + 時間パターン）

---

#### BC4: 睡眠ログ（感情 + 健康）

**シナリオ:**
「昨夜は悪い夢を見て、睡眠品質が悪かった」

**複合要素:**
- emotional_state: 悪い夢・感情的動揺
- health_vitals: 睡眠品質スコア

**判定ルール:**
→ **HEALTH_VITALS を主とする**

**理由:**
生体データ（睡眠品質）がコアなため、より制限的な health_vitals ルールを適用。

**適用例:**
- Health Coach は `health_vitals=FULL` なので完全開示
- Claude Personal は `health_vitals=HIDDEN` なので、感情部分のみ開示可能（「悪い夢を見た」）

---

#### BC5: 休日に一人でいる傾向

**シナリオ:**
「休日は一人でいることが多い。内向的な性格だからかな。」

**複合要素:**
- behavioral_patterns: 休日の行動頻度
- core_identity: 内向性スコア
- social_graph: 関係の少なさ（推測）

**判定ルール:**
→ **SOCIAL_GRAPH を主とする**

**理由:**
社会的孤立や関係的パターンに関わるため、最も制限的な社会関係グラフのルールを適用。

---

#### BC6: Spotify プレイリスト（興味 + 感情 + 社会関係）

**シナリオ:**
「友人Xと共有している 'Chill Evening' プレイリスト。去年から聞いてる。」

**複合要素:**
- interests_values: 音楽ジャンル・好み
- emotional_state: 聞く時間帯・気分
- social_graph: 友人X との関係・共有

**判定ルール:**
→ **SOCIAL_GRAPH を主とする（友人との共有時）、interests_values を主とする（個人的な聞き方時）**

**条件分岐:**
- 「友人との共有」という要素が強い → SOCIAL_GRAPH ルールを優先
- 「個人的な音楽嗜好」という要素のみ → INTERESTS_VALUES ルールを優先

**適用例:**
- `social_graph=HIDDEN`: 共有プレイリスト全体を非開示
- `social_graph=ANONYMIZED` + `interests_values=FULL`: 「person_hash_xyz とのプレイリスト」のみ

---

#### BC7: 推測される精神状態（複数の弱信号の統合）

**シナリオ:**
「最近、エピソード数が減り、心拍数が高く、夜中に目が覚めることが多い」

**複合要素:**
- episodic_memory: 活動量の低下
- health_vitals: 心拍・睡眠の悪化
- emotional_state: 推測される不安・うつ

**判定ルール:**
→ **HEALTH_VITALS + EMOTIONAL_STATE を主とする**

**理由:**
メンタルヘルスに直結するため、最も制限的なヘルス + 感情ルールを適用。

---

### 5.2 判定アルゴリズム

```
多カテゴリにまたがるデータが発見された場合:

1. 関連カテゴリをすべて列挙
2. 各カテゴリの開示粒度を確認
3. 「最も制限的なレベル」を適用
   - Hidden > Anonymized > Summary > Full

例:
  Data X が episodic_memory(Full) + health_vitals(Hidden) に属する
  → max_restriction = Hidden
  → Data X は Hidden (完全非開示)
```

---

## 6. Capability Token 構造

複数のAI/アプリへの権限を統一管理するトークンベースのシステム。

### 6.1 Token の構造

```python
{
  "issuer": "user_12345",              # トークン発行者（ユーザー自身）
  "subject": "claude",                 # 対象AI/アプリ
  "scope": {
    "name": "Claude Personal",
    "categories": {
      "core_identity": ["full", null],
      "episodic_memory": ["full", 90],
      ...
    },
    "expires_at": "2026-04-22T10:00:00Z"
  },
  "granted_at": "2026-04-15T10:00:00Z",
  "expires_at": "2026-04-22T10:00:00Z",
  "one_time": false,
  "nonce": "abc123def456",              # 再利用防止ID
  "signature": "hmac_sha256_..."        # 改ざん防止署名
}
```

### 6.2 トークンのライフサイクル

```
1. 発行 (Grant)
   ユーザーが「Claude に Personal scope を与える」と決定
   → Token を生成 → 署名 → 保存

2. 提示 (Present)
   Claude が SOUL へのアクセスをリクエスト
   → Token を提示

3. 検証 (Verify)
   Gateway が Token を受け取る
   → 署名検証 → 期限確認 → 有効性判定

4. 適用 (Apply)
   Token が有効なら、scope ルールに基づいて SOUL をフィルタリング

5. 期限切れ / 失効 (Revoke)
   expires_at を過ぎた → 自動失効
   または ユーザーが手動で取り消し
```

### 6.3 一時的なスコープ

`one_time=true` のトークンは、**1回の使用後に自動失効**。

```
用途例：
- 「このGPTとだけ今日の相談をしたい」
- 「Emergency scope で医療AIと1回だけやり取り」
```

---

## 7. Permission Gateway — 実装概要

SOULデータへのすべてのアクセスは Permission Gateway を通す。

### 7.1 Gateway のフロー

```
外部AI が SOUL リクエスト
    ↓
Capability Token を提示
    ↓
[Gateway] Token 署名検証
    ↓
期限切れ？
├─ Yes → Access Denied
└─ No → 次へ
    ↓
[Gateway] Scope を抽出
    ↓
Scope のカテゴリルールを適用
    ├─ Hidden → カテゴリを完全削除
    ├─ Anonymized → ハッシュ化・個人識別子削除
    ├─ Summary → 集計・平均化
    └─ Full → 期間制限を適用（days_limit）
    ↓
フィルタリング済み SOUL を返却
```

### 7.2 フィルタリング例

**入力:** 完全な SOUL
**Token:** Claude Personal
**出力:**

```json
{
  "version": "0.1.0",
  "owner_hash": "user_12345",
  "filtered_at": "2026-04-15T10:00:00Z",
  "categories": {
    "core_identity": {
      "granularity": "full",
      "days_limit": null,
      "data": {
        "openness": {"mu": 0.72, "sigma": 0.15},
        ...
      }
    },
    "episodic_memory": {
      "granularity": "full",
      "days_limit": 90,
      "data": {
        "recent": [
          // 直近90日のみ
        ]
      }
    },
    "emotional_state": {
      "granularity": "full",
      "days_limit": 7,
      "data": { ... }
    },
    "behavioral_patterns": {
      "granularity": "summary",
      "days_limit": null,
      "data": {
        "avg_sleep_hours": 7.5,
        ...
      }
    },
    "social_graph": {
      "granularity": "anonymized",
      "days_limit": 30,
      "data": {
        "relationships": [
          {
            "person_id_hash": "hash_abc123",
            "relationship_type": "close",
            "strength": 0.9
          },
          ...
        ]
      }
    }
    // health_vitals, location_movement は Hidden のため含まない
  }
}
```

---

## 8. UI / UX（管理画面）

Ghost-Printer はコア機を持たないため、スマホアプリで開示設定を管理。

### 8.1 必須3機能（Track C4）

#### 機能1: AI ごとのスコープ設定

```
┌──────────────────────────────┐
│ 開示スコープ管理              │
├──────────────────────────────┤
│ ✓ Claude Personal      ▼    │
│  ├─ Core Identity       ◐ Full
│  ├─ Episodic Mem.       ◐ Full (直近90日)
│  ├─ Emotional State     ◐ Full (直近7日)
│  ├─ Behavioral Pat.     ◑ Summary
│  ├─ Health Vitals       ✗ Hidden
│  ├─ Location/Move       ✗ Hidden
│  ├─ Social Graph        ◒ Anonymized (直近30日)
│  └─ Interests/Values    ◐ Full
│
│ □ Work Assistant       ▼
│ □ Health Coach         ▼
│ □ Minimal              ▼
│
│ [+ Create Custom]
└──────────────────────────────┘
```

#### 機能2: 一時的な共有

```
┌──────────────────────────────┐
│ 一時スコープ                  │
├──────────────────────────────┤
│ スコープ: [Emergency ▼]      │
│ 有効期限: [1時間 ▼]          │
│ AI/アプリ: [Health Crisis AI] │
│
│ [Share] [Cancel]
└──────────────────────────────┘
```

#### 機能3: 緊急停止

```
┌──────────────────────────────┐
│ セキュリティ                  │
├──────────────────────────────┤
│ アクティブなトークン: 3 個
│
│ ✓ Claude Personal (expires 2026-04-22)
│ ✓ Work Assistant  (expires 2026-04-18)
│ ✓ Health Coach    (expires 2026-04-30)
│
│ [🔴 Revoke All Tokens] (緊急停止)
│ [🔴 Revoke Selected]
└──────────────────────────────┘
```

---

## 9. 技術的な詳細

### 9.1 署名・検証

Token の改ざん防止のため HMAC-SHA256 署名を使用。

```python
payload = json.dumps(token.to_jwt_payload(), sort_keys=True)
signature = hmac.new(
    secret_key.encode(),
    payload.encode(),
    hashlib.sha256
).hexdigest()
```

秘密鍵はデバイス上で安全に保管（TEE / Secure Enclave）。

### 9.2 期間制限の実装

```python
# Token のスコープに記載された days_limit を確認
cutoff_date = datetime.now() - timedelta(days=days_limit)
filtered_episodes = [
    ep for ep in soul.episodic_memory.recent
    if datetime.fromisoformat(ep.timestamp) > cutoff_date
]
```

### 9.3 Anonymization ハッシュ関数

ハッシュ化にはユーザーごとの salt を使用し、同一ユーザー内では「同じ人は同じハッシュ」だが、別ユーザーからは識別不可にする。

```python
def anonymize_person_id(person_id: str, user_salt: str) -> str:
    combined = f"{person_id}:{user_salt}"
    return hashlib.sha256(combined.encode()).hexdigest()[:12]

# 結果: 「Alice」→「hash_abc123」（同一ユーザー内では常に同じ）
#      別ユーザーの「Alice」→「hash_xyz789」（異なるハッシュ）
```

---

## 10. セキュリティ考慮事項

### 10.1 脅威モデル

| 脅威 | 対策 |
|---|---|
| Token 改ざん | HMAC署名検証 |
| Token 再利用 | nonce (ワンタイムID) / one_time フラグ |
| 期限超過後のアクセス | Gateway で expires_at 確認 |
| Anonymization の逆算 | salt ベースのハッシュ化 |
| 複数AI への無断転送 | Token は subject ごと（転用不可） |

### 10.2 監査ログ

```json
{
  "timestamp": "2026-04-15T10:30:45Z",
  "event": "token_used",
  "subject": "claude",
  "scope": "Claude Personal",
  "categories_accessed": ["core_identity", "episodic_memory", ...],
  "result": "success"
}
```

---

## 11. 今後の拡張

### 11.1 Multi-Party Computation (MPC)

複数デバイス間で SOUL マージする際、中央集約なしに暗号化計算で結合。

### 11.2 Delegated Disclosure

「友人に『俺について知ってもらいたいこと』を事前セットして、友人が見に来たら自動開示」といった委任型開示。

### 11.3 条件付きスコープ

```
「Monday 9-17 は Work Assistant scope」
「その他は Claude Personal scope」
```

時間帯・文脈に応じた動的スコープ。

---

## 12. まとめ

| 要素 | 仕様 |
|---|---|
| **カテゴリ** | 8カテゴリ（Core Identity, Episodic Memory, 他） |
| **粒度** | 4段階（Full, Summary, Anonymized, Hidden） |
| **期間制限** | Optional（カテゴリごと） |
| **スコープテンプレート** | 5つ（Claude Personal, Work, Health, Minimal, Emergency） |
| **権限モデル** | Capability Token（署名付き、期間制限、revocable） |
| **フィルタリング** | Permission Gateway で一元管理 |
| **境界ケース** | 7 cases 定義、最も制限的なルール適用 |

---

## 13. 参考資料

- Ghost-Printer Handoff Doc (Section 5)
- SOUL フォーマット (ghost_printer_handoff.md Section 2)
- C2 Capability Token Design (to be linked)
- Permission Model Reference: Zerokit, Capsule
- Anonymization: k-anonymity, differential privacy

---

**Document Status:** Draft (C1 Early Priority)
**Next Action:** Implement Disclosure Gateway + integrate with soul_engine.py
**Review Cycle:** 2 weeks
