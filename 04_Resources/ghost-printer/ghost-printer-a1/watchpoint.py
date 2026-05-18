"""
Ghost-Printer — Watch Point System
    観測ポイントの生態系モデル

設計思想:
  Watch Pointは「生まれ・育ち・競争し・淘汰される」生態系として動作する。
  LLMが有用なパターンを発見するたびに新しいWPが生まれ、情報利得によって
  育ち、淘汰圧（最大数・ゼロヒット・冗長性）によって死ぬ。
  死んだWPの学習結果はSOULのcore_identityに蒸留される。

ライフサイクル:
  nascent  → 生まれたて（試用期間）
  active   → 情報を安定して提供している
  dormant  → 最近ヒットがないが過去に貢献した
  dying    → フィットネス閾値以下（次の淘汰で消える）
  culled   → 消滅（蒸留はSOULに残る）

フィットネス計算:
  fitness = information_gain × hit_rate × priority × recency_factor
           ^^^^^^^^^^^^^^^^^   ^^^^^^^^^   ^^^^^^^^   ^^^^^^^^^^^^^^^
           (観測で何が変わったか)  (頻度)  (LLM評価)   (時間減衰)

淘汰メカニズム:
  1. 自然減衰: 時間とともにrecency_factorが下がる
  2. 容量競争: max_active超過時、最弱WPが新WPに置換される
  3. 冗長剪定: 類似targetのWPが統合される
  4. ゼロヒット: 一定期間ヒットなし → 削除
  5. 試用期間落第: nascentでN回以内にhitがなければ削除
"""

import math
import json
import hashlib
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional, Dict, List, Any, Tuple
from enum import Enum


# ════════════════════════════════════════════════════════════════════════════════
# 1. 型定義
# ════════════════════════════════════════════════════════════════════════════════

class WPState(str, Enum):
    NASCENT = "nascent"    # 試用期間
    ACTIVE = "active"      # 通常動作
    DORMANT = "dormant"    # 休眠（復活可能）
    DYING = "dying"        # 淘汰対象
    CULLED = "culled"      # 消滅済み（蒸留されSOULへ）


class WPTrigger(str, Enum):
    """Watch Pointが生成されるトリガー"""
    TOPIC_CONCENTRATION = "topic_concentration"      # 特定トピックの偏り
    EMOTION_DRIFT = "emotion_drift"                  # 感情分布の偏り
    IMPORTANCE_SPIKE = "importance_spike"            # 重要度の集中
    PERSONALITY_UNCERTAINTY = "personality_uncertainty"  # σ急拡大
    VALUE_SHIFT = "value_shift"                      # 価値観の変化兆候
    LLM_SUGGESTED = "llm_suggested"                  # LLMが明示的に提案
    MANUAL = "manual"                                # 人間が手動で定義


@dataclass
class WatchPoint:
    """
    観測ポイント。SOULに第5層として格納される。
    """
    # ── アイデンティティ ──
    id: str                                  # 例: "wp_career_20260415"
    target: str                              # 観測対象（例: "career", "sleep_quality"）
    trigger: WPTrigger                       # 生成トリガー
    description: str = ""                    # 人間可読な説明

    # ── ライフサイクル ──
    state: WPState = WPState.NASCENT
    created_at: str = ""
    last_hit_at: str = ""
    observation_count: int = 0               # 総観測回数
    hit_count: int = 0                       # 意味のあるヒット数

    # ── 観測値の事前分布（ベイズ） ──
    prior_mu: float = 0.5
    prior_sigma: float = 0.30
    observations: List[float] = field(default_factory=list)  # 直近観測値（maxlen制限）

    # ── フィットネス指標 ──
    priority: float = 0.5                    # LLMが設定した初期重要度 [0-1]
    information_gain: float = 0.0            # 累積情報利得（平均KLダイバージェンス）
    last_observation_gain: float = 0.0       # 直近の情報利得

    # ── 観測対象次元（SOULのどの部分に作用するか） ──
    affects_dimensions: List[str] = field(default_factory=list)
    # 例: ["openness", "conscientiousness"]

    # ── 内部状態 ──
    probation_remaining: int = 3             # 試用期間残り（nascent時のみ）
    dormant_since: Optional[str] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["state"] = self.state.value
        d["trigger"] = self.trigger.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "WatchPoint":
        d = dict(d)
        d["state"] = WPState(d.get("state", "nascent"))
        d["trigger"] = WPTrigger(d.get("trigger", "manual"))
        return cls(**d)


# ════════════════════════════════════════════════════════════════════════════════
# 2. WatchPointManager — 生態系ロジック
# ════════════════════════════════════════════════════════════════════════════════

class WatchPointManager:
    """
    Watch Pointのライフサイクル全体を管理する。

    - propose():    新WP生成の候補を評価（トリガーチェック）
    - observe():    既存WPに観測値を記録し、フィットネスを更新
    - evolve():     1サイクルごとの進化処理（減衰・淘汰・統合）
    - distill():    消滅するWPの学習結果をSOULに蒸留
    """

    def __init__(
        self,
        max_active: int = 20,              # 同時アクティブ上限
        probation_trials: int = 3,         # 試用期間の観測回数
        min_hits_to_graduate: int = 1,     # 試用期間中の最低ヒット数
        fitness_floor: float = 0.05,       # これ未満は淘汰対象
        decay_half_life_days: float = 14.0,  # フィットネス半減期
        merge_similarity_threshold: float = 0.85,  # WP統合の閾値
        observation_window: int = 20,      # 各WPの観測履歴サイズ
    ):
        self.max_active = max_active
        self.probation_trials = probation_trials
        self.min_hits_to_graduate = min_hits_to_graduate
        self.fitness_floor = fitness_floor
        self.decay_half_life_days = decay_half_life_days
        self.merge_similarity_threshold = merge_similarity_threshold
        self.observation_window = observation_window

        self.watchpoints: Dict[str, WatchPoint] = {}
        self.culled_archive: List[dict] = []  # 蒸留前のアーカイブ

    # ── 読み取り ──

    def active(self) -> List[WatchPoint]:
        """アクティブ状態のWPのみ返す"""
        return [wp for wp in self.watchpoints.values()
                if wp.state in (WPState.NASCENT, WPState.ACTIVE, WPState.DORMANT)]

    def count(self) -> Dict[str, int]:
        """状態別WP数"""
        result = {s.value: 0 for s in WPState}
        for wp in self.watchpoints.values():
            result[wp.state.value] += 1
        return result

    # ── 新WP提案 ──

    def propose(
        self,
        target: str,
        trigger: WPTrigger,
        priority: float = 0.5,
        description: str = "",
        prior_mu: float = 0.5,
        prior_sigma: float = 0.30,
        affects_dimensions: Optional[List[str]] = None,
    ) -> Optional[WatchPoint]:
        """
        新しいWPの生成を提案する。

        - 既に類似targetのWPがある場合、そちらを強化して新規作成はスキップ
        - 容量超過時は、新WPの期待フィットネスが最弱WPを上回る場合のみ置換
        """
        # 1. 類似WP検索 → 強化して戻る
        existing = self._find_similar(target)
        if existing:
            existing.priority = min(1.0, existing.priority + 0.1 * priority)
            if existing.state == WPState.DORMANT:
                existing.state = WPState.ACTIVE  # 休眠復活
            return existing

        # 2. 容量チェック
        active_list = self.active()
        if len(active_list) >= self.max_active:
            # 最弱WPを探す
            weakest = min(active_list, key=lambda w: self.fitness(w))
            new_candidate_fitness = priority * 0.5  # 新WPの期待値（甘めに評価）
            if self.fitness(weakest) >= new_candidate_fitness:
                return None  # 新WP拒否
            # 最弱WPを淘汰対象にして場所を空ける
            weakest.state = WPState.DYING

        # 3. 新WP生成
        wp_id = self._generate_id(target)
        wp = WatchPoint(
            id=wp_id,
            target=target,
            trigger=trigger,
            description=description,
            priority=priority,
            prior_mu=prior_mu,
            prior_sigma=prior_sigma,
            affects_dimensions=affects_dimensions or [],
            created_at=_now(),
            state=WPState.NASCENT,
            probation_remaining=self.probation_trials,
        )
        self.watchpoints[wp_id] = wp
        return wp

    # ── 観測 ──

    def observe(
        self,
        wp_id: str,
        value: float,
        information_gain: float = 0.0,
    ) -> bool:
        """
        WPに観測を記録する。

        Args:
            wp_id: 観測対象のWP ID
            value: 観測値 [0-1]
            information_gain: この観測で得られた情報量（KL-div等）
        """
        wp = self.watchpoints.get(wp_id)
        if not wp or wp.state == WPState.CULLED:
            return False

        wp.observation_count += 1
        wp.observations.append(value)
        if len(wp.observations) > self.observation_window:
            wp.observations = wp.observations[-self.observation_window:]

        # 有意な観測（情報利得がある）= ヒット
        if information_gain > 0.01:
            wp.hit_count += 1
            wp.last_hit_at = _now()
            wp.last_observation_gain = information_gain
            # 累積平均
            wp.information_gain = (
                (wp.information_gain * (wp.hit_count - 1) + information_gain)
                / wp.hit_count
            )

        # 試用期間更新
        if wp.state == WPState.NASCENT:
            wp.probation_remaining -= 1
            if wp.probation_remaining <= 0:
                # 卒業判定
                if wp.hit_count >= self.min_hits_to_graduate:
                    wp.state = WPState.ACTIVE
                else:
                    wp.state = WPState.DYING  # 試用落第

        # 休眠復活
        elif wp.state == WPState.DORMANT and information_gain > 0.01:
            wp.state = WPState.ACTIVE
            wp.dormant_since = None

        return True

    # ── フィットネス計算 ──

    def fitness(self, wp: WatchPoint) -> float:
        """
        WPのフィットネスを計算する。

        fitness = information_gain × hit_rate × priority × recency_factor
        """
        if wp.state == WPState.CULLED:
            return 0.0

        # ヒット率（0-1）
        if wp.observation_count == 0:
            hit_rate = 0.5  # 未観測は中立
        else:
            hit_rate = wp.hit_count / wp.observation_count

        # 時間減衰（最後のヒットからの経過時間）
        recency_factor = self._recency_factor(wp.last_hit_at or wp.created_at)

        # 情報利得（最低でも0.1を担保してpriorityだけで生き残れる余地を残す）
        info_component = max(0.1, min(1.0, wp.information_gain * 5.0))

        return info_component * hit_rate * wp.priority * recency_factor

    # ── 1サイクルの進化処理 ──

    def evolve(self) -> Dict[str, Any]:
        """
        1サイクルごとの進化処理。

        - フィットネス閾値以下を dying に
        - 休眠判定（長期ヒットなし but 過去に貢献）
        - dying を culled に
        - 類似ペアの統合

        Returns:
            サイクルの統計情報
        """
        stats = {
            "transitioned_to_dormant": 0,
            "transitioned_to_dying": 0,
            "culled": [],
            "merged": [],
        }

        # 1. フィットネス評価と状態遷移
        for wp in list(self.watchpoints.values()):
            if wp.state == WPState.CULLED:
                continue

            f = self.fitness(wp)
            recency = self._recency_factor(wp.last_hit_at or wp.created_at)

            # NASCENTは試用期間中なのでfitness_floorによる淘汰から保護
            # （試用期間の合否は observe() 内で判定される）
            if wp.state == WPState.NASCENT:
                continue

            # ACTIVE → DORMANT: recencyが低いが情報利得は高い
            if wp.state == WPState.ACTIVE and recency < 0.3 and wp.information_gain > 0.05:
                wp.state = WPState.DORMANT
                wp.dormant_since = _now()
                stats["transitioned_to_dormant"] += 1

            # 任意状態 → DYING: フィットネスが閾値以下
            elif wp.state != WPState.DYING and f < self.fitness_floor:
                wp.state = WPState.DYING
                stats["transitioned_to_dying"] += 1

        # 2. 類似WPの統合
        merged = self._merge_similar()
        stats["merged"] = merged

        # 3. DYING → CULLED（蒸留対象）
        for wp in list(self.watchpoints.values()):
            if wp.state == WPState.DYING:
                self.culled_archive.append(wp.to_dict())
                stats["culled"].append(wp.id)
                wp.state = WPState.CULLED

        return stats

    # ── 蒸留（SOULに還元） ──

    def distill_culled(self, soul: dict) -> int:
        """
        CULLED状態のWPの学習結果をSOULのcore_identityに蒸留する。
        蒸留後、アーカイブからクリアする。

        Returns:
            蒸留したWP数
        """
        if not self.culled_archive:
            return 0

        distilled_count = 0
        for wp_dict in self.culled_archive:
            if wp_dict.get("hit_count", 0) < 2:
                continue  # ヒット不足は蒸留しない

            affects = wp_dict.get("affects_dimensions", [])
            observations = wp_dict.get("observations", [])
            if not affects or not observations:
                continue

            # 観測平均値 → 性格次元に弱く反映
            obs_mean = sum(observations) / len(observations)
            weight = min(0.3, wp_dict.get("information_gain", 0) * wp_dict.get("hit_count", 0) * 0.1)

            for dim_name in affects:
                dim = soul.get("core_identity", {}).get(dim_name)
                if not dim:
                    continue
                # 弱いベイズ更新相当の処理（σを広げずにμを微調整）
                new_mu = dim["mu"] * (1 - weight) + obs_mean * weight
                dim["mu"] = round(new_mu, 4)

            distilled_count += 1

        self.culled_archive = []  # クリア
        return distilled_count

    # ── シリアライズ ──

    def to_list(self) -> List[dict]:
        """SOULに書き戻すためのリスト形式"""
        return [wp.to_dict() for wp in self.watchpoints.values()
                if wp.state != WPState.CULLED]

    def from_list(self, data: List[dict]) -> None:
        """SOULから読み込み"""
        self.watchpoints = {}
        for d in data:
            wp = WatchPoint.from_dict(d)
            self.watchpoints[wp.id] = wp

    # ── 内部ユーティリティ ──

    def _generate_id(self, target: str) -> str:
        """WP IDを生成"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        slug = target.lower().replace(" ", "_")[:20]
        base = f"wp_{slug}_{timestamp}"
        # 衝突回避
        if base in self.watchpoints:
            suffix = hashlib.md5(str(datetime.now()).encode()).hexdigest()[:4]
            return f"{base}_{suffix}"
        return base

    def _find_similar(self, target: str) -> Optional[WatchPoint]:
        """類似targetを持つ既存WPを返す（正規化比較）"""
        t_norm = target.lower().strip()
        for wp in self.active():
            if wp.target.lower().strip() == t_norm:
                return wp
            # 簡易類似判定: 共通単語の比率
            w1 = set(t_norm.split("_"))
            w2 = set(wp.target.lower().split("_"))
            if w1 and w2:
                jaccard = len(w1 & w2) / len(w1 | w2)
                if jaccard >= self.merge_similarity_threshold:
                    return wp
        return None

    def _merge_similar(self) -> List[Tuple[str, str]]:
        """アクティブWPの類似ペアを統合する"""
        merged = []
        active_list = self.active()
        marked: set = set()

        for i, wp1 in enumerate(active_list):
            if wp1.id in marked:
                continue
            for wp2 in active_list[i+1:]:
                if wp2.id in marked:
                    continue
                w1 = set(wp1.target.lower().split("_"))
                w2 = set(wp2.target.lower().split("_"))
                if not w1 or not w2:
                    continue
                jaccard = len(w1 & w2) / len(w1 | w2)
                if jaccard >= self.merge_similarity_threshold:
                    # 弱い方 → 強い方へ統合
                    strong, weak = (wp1, wp2) if self.fitness(wp1) >= self.fitness(wp2) else (wp2, wp1)
                    strong.priority = min(1.0, (strong.priority + weak.priority * 0.5) / 1.5)
                    strong.information_gain = max(strong.information_gain, weak.information_gain)
                    strong.hit_count += weak.hit_count
                    weak.state = WPState.CULLED
                    self.culled_archive.append(weak.to_dict())
                    marked.add(weak.id)
                    merged.append((weak.id, strong.id))
        return merged

    def _recency_factor(self, iso_timestamp: str) -> float:
        """最終ヒットからの時間減衰（半減期ベース）"""
        if not iso_timestamp:
            return 1.0
        try:
            t = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
        except ValueError:
            return 1.0
        now = datetime.now(timezone.utc)
        elapsed_days = (now - t).total_seconds() / 86400.0
        if elapsed_days <= 0:
            return 1.0
        return 0.5 ** (elapsed_days / self.decay_half_life_days)


# ════════════════════════════════════════════════════════════════════════════════
# 3. Watch Point 生成ルール（Soul Cortexから呼ばれる）
# ════════════════════════════════════════════════════════════════════════════════

class WatchPointRules:
    """
    LLMの観測結果から、どんなWPが必要そうかを判定する。
    これは静的ルールだが、CORTEX.binで閾値を調整可能。
    """

    def __init__(
        self,
        topic_concentration_threshold: float = 0.6,
        emotion_drift_threshold: float = 0.3,
        importance_spike_count: int = 3,
        importance_spike_threshold: float = 0.7,
        personality_sigma_spike: float = 0.1,
    ):
        self.topic_threshold = topic_concentration_threshold
        self.emotion_drift = emotion_drift_threshold
        self.importance_count = importance_spike_count
        self.importance_threshold = importance_spike_threshold
        self.sigma_spike = personality_sigma_spike

    def check_topic_concentration(
        self,
        topic_history: List[Dict[str, float]],
    ) -> List[Dict[str, Any]]:
        """
        最近のトピック分布が特定トピックに集中していれば候補を返す
        """
        if len(topic_history) < 3:
            return []
        # 直近N件の平均
        agg: Dict[str, float] = {}
        for dist in topic_history[-10:]:
            for k, v in dist.items():
                agg[k] = agg.get(k, 0) + v

        total = sum(agg.values())
        if total == 0:
            return []

        candidates = []
        for topic, weight in agg.items():
            ratio = weight / total
            if ratio >= self.topic_threshold:
                candidates.append({
                    "target": topic,
                    "trigger": WPTrigger.TOPIC_CONCENTRATION,
                    "priority": min(1.0, ratio),
                    "description": f"最近「{topic}」の話題が頻出（{ratio:.0%}）",
                })
        return candidates

    def check_importance_spike(
        self,
        recent_episodes: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """直近に高重要度エピソードが集中していれば候補を返す"""
        recent = recent_episodes[-10:]
        high_imp = [e for e in recent if e.get("importance", 0) >= self.importance_threshold]
        if len(high_imp) < self.importance_count:
            return []

        # 共通トピックを抽出
        topic_counts: Dict[str, int] = {}
        for ep in high_imp:
            for t in ep.get("topic_distribution", {}).keys():
                topic_counts[t] = topic_counts.get(t, 0) + 1

        candidates = []
        for topic, count in topic_counts.items():
            if count >= self.importance_count:
                candidates.append({
                    "target": f"{topic}_decisions",
                    "trigger": WPTrigger.IMPORTANCE_SPIKE,
                    "priority": 0.75,
                    "description": f"「{topic}」関連の重要エピソードが{count}件集中",
                })
        return candidates

    def check_personality_uncertainty(
        self,
        core_identity: Dict[str, Dict[str, float]],
        previous_identity: Optional[Dict[str, Dict[str, float]]] = None,
    ) -> List[Dict[str, Any]]:
        """性格σが急拡大した次元を検出"""
        if not previous_identity:
            return []
        candidates = []
        for dim_name, current in core_identity.items():
            prev = previous_identity.get(dim_name)
            if not prev:
                continue
            sigma_diff = current.get("sigma", 0) - prev.get("sigma", 0)
            if sigma_diff >= self.sigma_spike:
                candidates.append({
                    "target": f"{dim_name}_volatility",
                    "trigger": WPTrigger.PERSONALITY_UNCERTAINTY,
                    "priority": 0.6,
                    "description": f"{dim_name}のσが急拡大（{sigma_diff:+.3f}）",
                    "affects_dimensions": [dim_name],
                })
        return candidates


# ════════════════════════════════════════════════════════════════════════════════
# ユーティリティ
# ════════════════════════════════════════════════════════════════════════════════

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ════════════════════════════════════════════════════════════════════════════════
# デモ
# ════════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("═══ Ghost-Printer Watch Point Ecosystem Demo ═══\n")

    mgr = WatchPointManager(max_active=5)

    # Scenario: 様々なWPを生成して淘汰サイクルを観察

    # 1. LLMが「最近キャリアの話題が多い」と判断
    wp1 = mgr.propose(
        target="career",
        trigger=WPTrigger.TOPIC_CONCENTRATION,
        priority=0.8,
        description="最近キャリアの話が多い",
        affects_dimensions=["conscientiousness", "risk_tolerance"],
    )
    print(f"[BORN] {wp1.id} priority={wp1.priority}")

    # 2. 睡眠パターン
    wp2 = mgr.propose(
        target="sleep_quality",
        trigger=WPTrigger.IMPORTANCE_SPIKE,
        priority=0.6,
        description="睡眠関連のエピソードが増加",
    )
    print(f"[BORN] {wp2.id} priority={wp2.priority}")

    # 3. ノイズWP（あまり有用でない）
    wp3 = mgr.propose(
        target="weather_talk",
        trigger=WPTrigger.TOPIC_CONCENTRATION,
        priority=0.3,
        description="天気の話題（ノイズ候補）",
    )
    print(f"[BORN] {wp3.id} priority={wp3.priority}")

    # --- 観測サイクル ---
    print("\n── Observation Cycle 1 ──")
    # キャリアWPは良いヒット
    mgr.observe(wp1.id, 0.75, information_gain=0.08)
    # 睡眠WPも弱めのヒット
    mgr.observe(wp2.id, 0.4, information_gain=0.03)
    # 天気WPは空振り
    mgr.observe(wp3.id, 0.5, information_gain=0.001)

    for wp in mgr.active():
        print(f"  {wp.id:50s} state={wp.state.value:8s} fitness={mgr.fitness(wp):.3f}")

    # 試用期間を終わらせるためにさらに観測
    print("\n── Observation Cycle 2-3 ──")
    mgr.observe(wp1.id, 0.8, information_gain=0.09)
    mgr.observe(wp2.id, 0.45, information_gain=0.04)
    mgr.observe(wp3.id, 0.5, information_gain=0.0)  # 完全空振り
    mgr.observe(wp1.id, 0.72, information_gain=0.06)
    mgr.observe(wp2.id, 0.5, information_gain=0.02)
    mgr.observe(wp3.id, 0.5, information_gain=0.0)

    for wp in mgr.watchpoints.values():
        print(f"  {wp.id:50s} state={wp.state.value:8s} fitness={mgr.fitness(wp):.3f} hits={wp.hit_count}/{wp.observation_count}")

    # --- 進化処理（淘汰） ---
    print("\n── Evolution (Natural Selection) ──")
    stats = mgr.evolve()
    print(f"  Transitioned to dormant: {stats['transitioned_to_dormant']}")
    print(f"  Transitioned to dying:   {stats['transitioned_to_dying']}")
    print(f"  Culled:                   {stats['culled']}")
    print(f"  Merged:                   {stats['merged']}")

    print("\n── Active WatchPoints After Evolution ──")
    for wp in mgr.active():
        print(f"  {wp.id:50s} state={wp.state.value:8s} fitness={mgr.fitness(wp):.3f}")

    print(f"\n  Total: {mgr.count()}")

    # 容量競争テスト
    print("\n── Capacity Competition Test ──")
    for i in range(10):
        wp = mgr.propose(
            target=f"test_topic_{i}",
            trigger=WPTrigger.TOPIC_CONCENTRATION,
            priority=0.4 + i * 0.05,
            description=f"テスト候補 {i}",
        )
        if wp:
            print(f"  [ACCEPTED] wp={wp.target} priority={wp.priority:.2f}")
        else:
            print(f"  [REJECTED] test_topic_{i} - 容量満杯 & 既存WPが強い")

    print(f"\n  Final count: {mgr.count()}")

    # SOUL蒸留
    print("\n── Distillation to SOUL ──")
    # ダミーSOUL
    dummy_soul = {
        "core_identity": {
            "openness":          {"mu": 0.5, "sigma": 0.3},
            "conscientiousness": {"mu": 0.5, "sigma": 0.3},
            "risk_tolerance":    {"mu": 0.5, "sigma": 0.3},
        }
    }
    distilled = mgr.distill_culled(dummy_soul)
    print(f"  Distilled {distilled} WPs into SOUL")
    for dim, val in dummy_soul["core_identity"].items():
        print(f"    {dim:20s} μ={val['mu']:.4f}, σ={val['sigma']:.4f}")

    print("\n═══ Demo Complete ═══")
