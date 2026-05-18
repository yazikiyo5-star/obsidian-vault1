# A7 — 適応型 Watch Point 仕様書

**更新日:** 2026-05-04
**ステータス:** Draft (実装は本仕様と並行)
**前提:** A1 で実装済みの WatchPoint 生態系 (`watchpoint.py`) を温存しつつ、 LLM
が新規 WP を自動提案する経路を追加する。 memory に「2026-04-22 から再設計中」と
記録があった部分への結論。
**関連:** `watchpoint.py` (生態系本体), `extractor.py` (LLM 呼出パターン参考),
`specs/a6_soul_binary_format.md` (Persistence)

---

## 0. 設計目的

固定 3 ルール (TOPIC_CONCENTRATION / IMPORTANCE_SPIKE / PERSONALITY_UNCERTAINTY)
だけでは「ユーザ固有の追跡したい行動パターン」を発見しきれない。たとえば:

- 「最近、 締切前夜に深夜作業が増えている」
- 「平日は内向的だが週末に社交的になる」
- 「特定の人物 (alice) と会った後の感情が変動しがち」

こうした **複合パターン** は、 トピック分布や σ 急拡大という単一指標では
捕捉できない。 LLM (Bonsai 1.7B) に「最近の SOUL を読んで、 追跡したい新しい
観測ポイントを提案してね」と問いかけることで、 適応的に WP 集合を拡張する。

---

## 1. 二段構成のポリシー

```
                ┌──────────── AdaptiveWatchPointPolicy ──────────────┐
                │                                                       │
   SOUL ────────►   ┌───────────────┐    ┌──────────────────┐           │
   recent           │ WatchPointRules│   │  LlmWpProposer   │           │
   episodes         │  (固定3ルール) │   │  (LLM 提案)       │           │
   existing_wps     └───────────────┘    └──────────────────┘           │
                       │                       │                        │
                       └────────┬──────────────┘                        │
                                ▼                                        │
                  WatchPointManager.propose() で実 WP 化                 │
                                ▼                                        │
                         active WP set 更新                              │
                └─────────────────────────────────────────────────────────┘
```

### 1.1 ポリシー切替

| Policy | 説明 | 用途 |
|--------|------|------|
| `RULES_ONLY` | 既存3ルールのみ。 LLM を呼ばない | LLM 不在環境 / 低消費電力モード / 既存挙動互換 |
| `LLM_ONLY` | LLM のみが提案 | 実機で Bonsai が安定したら推奨 |
| `HYBRID` (default) | ルールで安全網を張りつつ、 残り枠を LLM で埋める | 通常運用 |

切替は `AdaptiveWatchPointPolicy(policy=...)` で指定。 CORTEX.bin に
`watchpoint_policy` フィールドを追加して試行錯誤可能 (B3 段階)。

### 1.2 ハイブリッド時の処理順

1. ルールが候補集合 R (最大数件) を返す → そのまま `propose()`
2. 「あと何個まで提案を受け入れられるか」を計算: `slots = max_new - len(R)`
3. LLM に `slots` 個の追加候補を求める
4. LLM 応答を JSON パース → 各候補に `propose()` を呼ぶ
5. 既に類似 WP があれば `_find_similar()` 経由で強化扱い (新規不発)

> **不変条件:** LLM が呼べなくても (HTTP error / parse fail) システムは継続動作。
> ルールベースの提案は必ず通る。

---

## 2. LLM プロンプトと出力形式

### 2.1 プロンプト構造 (Bonsai 1.7B 想定)

```
あなたは Ghost-Printer の Watch Point 提案者です。SOUL の最近の動きから、
追跡すべき新しい観測対象を提案してください。

## 現在の SOUL 状態
性格 (μ ± σ):
  openness: 0.78 ± 0.10
  conscientiousness: 0.69 ± 0.12
  curiosity: 0.59 ± 0.15
  ...
最近のトピック (上位5): {ai, hardware, reading, presentation, late_night}
最近の価値観: {self-care, creativity, perseverance}

## 現在の Watch Points (重複しないこと)
  - career_progress (priority=0.8, hits=5/8)
  - sleep_quality (priority=0.6, hits=2/4)

## 最近のエピソード (5件)
1. [2026-04-15] 深夜まで試作品を作っていた (importance=0.75, joy)
2. [2026-04-14] チームで発表 (importance=0.6, excitement)
3. ...

## 提案ガイドライン
- 既存 WP と target が重複しないものを最大 N 件
- 性格次元 (openness / conscientiousness / extraversion / agreeableness /
  neuroticism / curiosity / creativity / empathy / risk_tolerance / independence)
  のいずれかと結びつく観測対象
- target は短い英小文字スラッグ (snake_case, 30 文字以内)
- priority は 0.3-0.9
- description は 60 文字以内の日本語

## 出力フォーマット (JSON 配列のみ、 説明文不要)
[
  {
    "target": "weekend_socializing",
    "description": "週末に社交イベントが増える兆候",
    "priority": 0.6,
    "affects_dimensions": ["extraversion", "agreeableness"],
    "rationale": "平日は内向的、 週末のエピソードに 'meet' トピックが集中"
  }
]
```

### 2.2 出力 JSON スキーマ (LlmProposal)

```python
@dataclass
class LlmProposal:
    target: str                      # snake_case, 30 char 以内
    description: str                 # 人間可読、 60 char 以内
    priority: float                  # 0.3..0.9 にクランプ
    affects_dimensions: list[str]    # core_identity の dim 名のサブセット
    rationale: str = ""              # LLM の理由付け (デバッグ用)
    prior_mu: float = 0.5            # ベイズ事前分布
    prior_sigma: float = 0.30
```

### 2.3 LLM 応答パーサの方針

1. 最初に CBOR 風のコードブロック ```` ```json ... ``` ```` を探す
2. 見つからなければ `[` から始まる JSON 配列を直接探す
3. 各エントリで required field (`target`, `description`, `priority`) が
   揃っていなければ skip
4. `affects_dimensions` の各値が known dim 名でなければ除外
5. `priority` はクランプ ([0.3, 0.9])
6. `target` は正規化 (lowercase, 空白→アンダースコア, 30 char 切詰)

不正な JSON / 完全失敗時は **空配列を返す** (= ルール側で埋める)。

---

## 3. WatchPointManager との統合

### 3.1 既存 API は壊さない

- `WatchPointManager.propose()` は変更なし
- `WatchPointRules.check_*()` は変更なし
- 新クラス `LlmWpProposer` と `AdaptiveWatchPointPolicy` は **新規モジュール
  `watchpoint_llm.py`** に配置。 既存テストが回帰しない

### 3.2 統合点

```python
from watchpoint_llm import AdaptiveWatchPointPolicy, Policy

policy = AdaptiveWatchPointPolicy(
    rules=WatchPointRules(),
    proposer=LlmWpProposer(llm_call=ollama_call),
    policy=Policy.HYBRID,
    max_new_per_cycle=3,
)

# Soul Cortex の evolve_watchpoints() の中で:
proposals = policy.gather_proposals(
    soul=current_soul,
    existing_wps=mgr.active(),
    topic_history=topic_dist_recent,
    previous_identity=last_identity_snapshot,
)
for p in proposals:
    mgr.propose(**p)
```

### 3.3 LLM 呼出抽象

`LlmWpProposer` は **コーラブルを依存性注入** する。

```python
class LlmWpProposer:
    def __init__(self, llm_call: Callable[[str], str]):
        self.llm_call = llm_call  # prompt -> response
```

これにより:
- 本番: `extractor._call_ollama` のラッパを渡す
- テスト: モック関数を渡す
- フォールバック: 常に空文字を返すダミー関数を渡してもクラッシュしない

---

## 4. レート制御と消費電力

LLM 呼出は計算高いので無制限に呼ばない:

| 設定 | 値 | 理由 |
|------|-----|------|
| `cycle_interval_minutes` | 60 (default) | evolve サイクル毎に呼ぶのは過剰 |
| `min_episodes_since_last_call` | 5 | 直近に動きが無ければ呼ばない |
| `max_new_per_cycle` | 3 | 1 サイクルで生成する WP 数の上限 |
| `prompt_token_budget` | ~800 | Bonsai 1.7B の context が短いので |

`should_invoke_llm()` 関数を提供。 `False` ならルールのみで進行。

---

## 5. 既存生態系との相互作用

### 5.1 LLM 提案でも淘汰圧は同じ

LLM が提案した WP も `WPState.NASCENT` でスタートし、 `probation_trials=3` の
試用期間を経る。 ヒット率 / fitness が低ければ淘汰される。 LLM が提案したからと
いって特権はない。

### 5.2 蒸留もそのまま

LLM 由来の WP が culled になっても `distill_culled()` で core_identity に
反映される。 メタデータの `trigger=LLM_SUGGESTED` は保持されるので、 後で
分析できる。

### 5.3 Repeat suggestion 抑制

LLM が同じターゲットを繰り返し提案するのを防ぐため、 `LlmWpProposer.recent_suggestions`
リスト (直近 30 件) を持って:

- 過去 7 日に同 target を提案済みなら除外
- 過去 30 日に culled された WP の target は除外 (生態系判断を尊重)

---

## 6. 失敗モードと対策

### 6.1 個別失敗

| 失敗 | 対策 |
|------|------|
| LLM HTTP error | `try/except` で握り潰す → 空配列返す → ルールベースで継続 |
| LLM 応答が JSON でない | 部分パース、 抽出可能なエントリのみ採用 |
| `affects_dimensions` に未知の dim | エントリ全体は採用、 未知 dim だけ捨てる |
| `priority` が範囲外 | [0.3, 0.9] にクランプ |
| LLM が同じ提案を繰り返す | recent_suggestions で抑制 (§5.3) |
| LLM が既存 WP の真似 | `existing_targets` セットでパース時に除外 |
| プロンプトが長すぎる (token超過) | episode サマリの行数 / WP 表示数を削減 |

### 6.2 LLM 永久死亡時の自己治癒 (2026-05-04 実装)

**問題**: 単発失敗ではなく LLM が永久に停止した状態 (Bonsai プロセス OOM、 モデル
ファイル破損、 プロセス未起動) が続く場合、 「サイレント劣化」(ルール提案だけが
通り、 ユーザに気付かれず LLM 提案ゼロが続く) が起きる。

**対策の 4 層構造:**

#### (A) `LlmHealthTracker` で死亡検知

状態機械:

```
                      consecutive_failures < 3
        ┌──────────────────────────────────────────┐
        │                                              │
        ▼                                              │
   ┌─────────┐  3連続失敗  ┌──────────┐ 24h 成功なし  ┌──────┐
   │ HEALTHY │ ──────────▶ │ DEGRADED │ ─────────────▶│ DEAD │
   └─────────┘              └──────────┘                 └──────┘
       ▲                          ▲                          │
       │                          │                          │
       │                          └─ probe で 1 回成功 ◀────┤
       └──────── 通常呼出で 1 回成功 ────────────────────────┘
```

- 閾値はデフォルト 3 連続失敗 / 24h 成功なし。 `degraded_threshold` /
  `dead_threshold_hours` で外から差替可
- 一度も成功していない場合は `created_at` を基準に 24h 判定
- `record_success()` は任意状態から HEALTHY に戻す
- 状態遷移は Python `logging` で `WARNING` ログ出力 (systemd journal で可視化)

#### (B) DEAD 状態では LLM を呼ばない

`LlmWpProposer(skip_when_dead=True)` (default) の場合、 `is_dead()` 時は
`propose_watchpoints()` が即空配列を返す。 5-10 秒のブロックを毎サイクル
無駄にしない。 ルール経路は通常通り動く。

#### (C) `probe_llm_health()` で復帰判定

DEAD 状態を打破するための独立した健康確認。 軽い prompt
(`"Reply with the single word OK"`) で LLM の生死を試す。

```python
# 1 時間ごとに別 timer から呼ぶ想定
ok = probe_llm_health(proposer)
# 成功 → proposer.health は HEALTHY に復帰 → 次の evolve サイクルから
#   通常 LLM 提案が再開される
```

成功したら `record_success()` で HEALTHY に戻す。 失敗してもただ次のプローブを
待つだけで通常 evolve サイクルには影響しない。

#### (D) `ChainedLlmCall` で多段フォールバック

primary が DEAD のとき自動で secondary に逃がす。

```python
chained = ChainedLlmCall(
    primary=bonsai_call,        # 1.7B, 高品質
    secondary=qwen05b_call,     # 0.5B, 安定 (~350MB)
)
proposer = LlmWpProposer(llm_call=chained)
```

挙動:
- primary HEALTHY/DEGRADED → primary を試す。 失敗ならこの 1 コールだけ secondary
- primary DEAD → 即 secondary
- primary 復帰 (probe 成功) → 次回から primary に戻る

`chained.fallback_count` で secondary 経由回数を観測可能。

### 6.3 ユーザ可視化

`main.py --status` で表示する想定 (実装は次フェーズ):

```
═══ LLM Health ═══
  Primary (bonsai-1.7b):    🟡 DEGRADED
    last_success_at:        2026-05-04 02:41 UTC (3.2 hours ago)
    consecutive_failures:   2
    failure_rate:           18% (24 invocations, 4 failures)
    last_failure_reason:    TimeoutError
  Secondary (qwen-0.5b):    🟢 HEALTHY
  Fallback events (24h):    1
```

`proposer.health.status_summary()` が辞書を返すので、 UI 側で整形するだけ。

### 6.4 永続化 (現フェーズ未配線)

`LlmHealthTracker.to_dict()` / `from_dict()` で JSON シリアライズ可能。
将来 `data/llm_health.json` に書き出して再起動後も状態を保つ予定 (現状は
プロセス再起動で HEALTHY からやり直し)。 これでも、 死亡時の検知 → 数サイクルで
DEAD 認定 → probe で復帰、 のループは即座に再開できるので実害は小さい。

### 6.5 リカバリー保証

- **基幹データ (SOUL / WP / 観測履歴)**: 100% 維持
- **継続中の複合パターン**: LLM 復帰後の evolve で拾える
- **死亡中の一過性パターン**: **拾えない** (原理的に)。 ShadowStorage に raw_text
  が 30 日残るので、 将来 `retrospective_propose()` 機構を足せば軽減可能

### 6.6 LLM プロセス自動再起動 (Tier 1, 2026-05-04 実装)

`probe_llm_health` で「死んでいる」と分かっても、 LLM プロセスが永遠に
復活しないケース (Bonsai が OOM kill された / model file ロード失敗で起動
直後に exit / メモリリークでハング) では待っていても直らない。 そこで
**外部コマンドで LLM を再起動する** マネージャを実装した。

#### `LlmRestartManager` の責務

- DEAD 状態が `min_dead_minutes` (default 5 分) 続いたら restart 検討
- `cooldown_minutes` (default 10 分) で連続 restart の暴走を防ぐ
- `max_restarts_per_hour` (default 3) で 1 時間の上限を設定
- subprocess.timeout で restart コマンド自体のハングも検知
- `dry_run=True` で動作確認モード (実コマンド未実行)

```python
restarter = LlmRestartManager(
    restart_command=["sudo", "systemctl", "restart", "bonsai.service"],
    health=proposer.health,
    min_dead_minutes=5.0,
    max_restarts_per_hour=3,
    cooldown_minutes=10.0,
)

# systemd timer から定期 (例: 5 分毎) に呼出
attempt = restarter.maybe_restart()
if attempt and attempt.success:
    time.sleep(30)          # bonsai が起きる時間
    probe_llm_health(proposer)
```

#### systemd 連携 (`SETUP_OPI3B.md §8` 参照)

3 つの自動再起動レイヤを重ねる:

| レイヤ | 手段 | 守るもの |
|--------|------|---------|
| **L0** | `bonsai.service` の `Restart=always` | LLM プロセスが落ちたら 10 秒で systemd が再起動 |
| **L1** | `ghost-printer.service` の `Restart=always` + `WatchdogSec=120s` | 本体プロセスのクラッシュ・ハングから復帰 |
| **L2** | `ghost-printer-probe.timer` (1h 毎) + `LlmRestartManager` | systemd の Restart で直らない状態 (= 起動直後 exit ループ等) を検知して `systemctl restart bonsai.service` を叩く |

L0 が一次防衛、 L2 は L0 で直らない病理 (例: 設定ミスで起動 → 即 exit を
無限繰り返し → systemd が StartLimitBurst で諦める) を見つけて手動再起動
コマンドで再起する。

#### 安全装置のテスト 14 件

- 健康時は呼ばない / DEAD 継続未満で skip / 閾値超で実行
- dry_run で実コマンド非実行 / 実コマンド成功 / 失敗 (exit≠0) / コマンド不在 / timeout
- cooldown / rate limit / 履歴 100 件制限 / stats 集計
- 実 restart 後の probe で health 復帰 / 空コマンド拒否

### 6.7 Tier 2 以上は実機データで設計

「**おかしな挙動**」(valid 応答だが質が低い / 同じ提案を繰り返すループ /
緩やかなメモリリーク) の検知と自動補正は Tier 2 として設計案を残すが、
**実機で実際にどんな壊れ方をするかを観測してから** 設計する方が筋が良い。
Pre-MVP では本仕様までで完了とし、 SD 到着後の実データを基に Tier 2 を別
仕様書 (A8) で起こす。

---

## 7. テスト方針

| テスト | 確認内容 |
|--------|----------|
| プロンプト構築 | SOUL state / 既存 WP / 最近 episode が含まれている |
| 正常 JSON パース | 3 件の候補が正しく `LlmProposal` 化される |
| code block 付き応答 | ```` ```json ... ``` ```` が剥がせる |
| 部分壊れ JSON | 壊れたエントリは skip、 残りは採用 |
| 既存 WP との dedup | `existing_targets` に含まれる target は除外 |
| 未知 dim を含む候補 | 該当 dim だけ捨て、 残った dim で採用 |
| `priority` クランプ | 1.5 → 0.9, -0.2 → 0.3 |
| HYBRID で枠が埋まる | rules 2 件 + LLM 1 件 = 合計 3 件 |
| LLM 失敗時の継続 | exception を投げる llm_call → ルール提案だけが返る |
| recent_suggestions 抑制 | 同 target 再提案で除外される |

---

## 8. 段階的移行

| フェーズ | 状態 | LLM 呼出 |
|---------|------|---------|
| Pre-MVP (現在) | 設計と実装、 モック LLM でテスト | 不要 |
| MVP (SD到着後) | Bonsai 1.7B を実機で接続 | 実呼出 |
| 製品版 | CORTEX.bin で `policy` を切替可能に | 実呼出 |

実機で Bonsai が遅すぎる場合は `Policy.RULES_ONLY` にフォールバックする。

---

## 9. 未決事項

| # | 項目 | 第一案 |
|---|------|--------|
| Q1 | LLM 呼出を非同期にするか | 同期 (シンプル、 Bonsai はローカル推論なので待ち時間問題なし) |
| Q2 | 提案 1 件あたりのコスト見積 | プロンプト ~600 token + 応答 ~200 token = Bonsai で 5-10 秒/呼出 |
| Q3 | 失敗時の retry | リトライしない (次サイクルでまた呼ばれる) |
| Q4 | プロンプト国際化 | 日本語 (ユーザは日本語話者) |
| Q5 | rationale フィールドの保存 | WP の `description` に組み込む (別フィールドにせず) |

すべて第一案で進める。 反対意見があれば本仕様を更新。

---

## 10. 次のステップ

1. **本仕様承認後**: `watchpoint_llm.py` のプロト実装 (mock LLM でテスト)
2. **実装後**: `test_watchpoint_llm.py` でハイブリッドポリシー検証
3. **SD 到着後**: 実 Bonsai 接続と prompt token budget の調整
4. **CORTEX 連携 (後)**: `WatchPointConfig` に `policy` フィールド追加、 試行錯誤可能化
