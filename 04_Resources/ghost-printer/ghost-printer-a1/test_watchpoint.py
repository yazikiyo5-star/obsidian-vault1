"""
Ghost-Printer Watch Point System — Test Suite
    生態系モデル（生成・観測・進化・淘汰・蒸留）の挙動検証

テストカテゴリ (合計 38 件):
  WP-*  WatchPoint データクラス        (5件)
  PR-*  propose (新WP生成)              (6件)
  OB-*  observe (観測)                  (6件)
  FT-*  fitness 計算                    (5件)
  EV-*  evolve (進化サイクル)            (5件)
  DS-*  distill_culled (蒸留)           (4件)
  RL-*  WatchPointRules                 (5件)
  ML-*  マージ・ライフサイクル統合       (2件)
"""

import time
import pytest
from datetime import datetime, timezone, timedelta

from watchpoint import (
    WatchPoint,
    WatchPointManager,
    WatchPointRules,
    WPState,
    WPTrigger,
    _now,
)


# ════════════════════════════════════════════════════════════════════════════════
# Fixtures
# ════════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def mgr():
    return WatchPointManager(
        max_active=5,
        probation_trials=3,
        min_hits_to_graduate=1,
        fitness_floor=0.05,
        decay_half_life_days=14.0,
    )


@pytest.fixture
def mgr_with_wp(mgr):
    wp = mgr.propose(
        target="career",
        trigger=WPTrigger.TOPIC_CONCENTRATION,
        priority=0.7,
        affects_dimensions=["conscientiousness"],
    )
    return mgr, wp


# ════════════════════════════════════════════════════════════════════════════════
# WP-*: WatchPoint データクラス
# ════════════════════════════════════════════════════════════════════════════════

class TestWatchPointDataclass:

    def test_WP_01_default_state(self):
        """WP-01: 生成直後はNASCENT"""
        wp = WatchPoint(id="test", target="x", trigger=WPTrigger.MANUAL)
        assert wp.state == WPState.NASCENT
        assert wp.observation_count == 0
        assert wp.hit_count == 0

    def test_WP_02_to_dict_serialization(self):
        """WP-02: to_dict() でstate/triggerが文字列化される"""
        wp = WatchPoint(id="t1", target="y", trigger=WPTrigger.LLM_SUGGESTED)
        d = wp.to_dict()
        assert d["state"] == "nascent"
        assert d["trigger"] == "llm_suggested"
        assert d["id"] == "t1"

    def test_WP_03_from_dict_roundtrip(self):
        """WP-03: to_dict → from_dict で復元できる"""
        wp = WatchPoint(
            id="t2", target="z", trigger=WPTrigger.VALUE_SHIFT,
            priority=0.77, prior_mu=0.4, observations=[0.1, 0.2],
        )
        wp.state = WPState.ACTIVE
        d = wp.to_dict()
        restored = WatchPoint.from_dict(d)
        assert restored.id == wp.id
        assert restored.state == WPState.ACTIVE
        assert restored.trigger == WPTrigger.VALUE_SHIFT
        assert restored.priority == pytest.approx(0.77)
        assert restored.observations == [0.1, 0.2]

    def test_WP_04_enum_values(self):
        """WP-04: Enum値が期待通り"""
        assert WPState.NASCENT.value == "nascent"
        assert WPState.CULLED.value == "culled"
        assert WPTrigger.TOPIC_CONCENTRATION.value == "topic_concentration"

    def test_WP_05_default_lists(self):
        """WP-05: デフォルトのobservations/affects_dimensionsは独立している"""
        wp1 = WatchPoint(id="a", target="a", trigger=WPTrigger.MANUAL)
        wp2 = WatchPoint(id="b", target="b", trigger=WPTrigger.MANUAL)
        wp1.observations.append(0.5)
        wp1.affects_dimensions.append("openness")
        # wp2 は影響を受けない（mutable defaultのバグチェック）
        assert wp2.observations == []
        assert wp2.affects_dimensions == []


# ════════════════════════════════════════════════════════════════════════════════
# PR-*: propose (新WP生成)
# ════════════════════════════════════════════════════════════════════════════════

class TestPropose:

    def test_PR_01_basic_proposal(self, mgr):
        """PR-01: 新WPが提案できる"""
        wp = mgr.propose(
            target="sleep",
            trigger=WPTrigger.IMPORTANCE_SPIKE,
            priority=0.5,
        )
        assert wp is not None
        assert wp.target == "sleep"
        assert wp.state == WPState.NASCENT
        assert wp.id in mgr.watchpoints

    def test_PR_02_similar_target_merges(self, mgr):
        """PR-02: 同一targetは既存を強化して新規作成しない"""
        wp1 = mgr.propose(target="sleep", trigger=WPTrigger.MANUAL, priority=0.5)
        wp2 = mgr.propose(target="sleep", trigger=WPTrigger.MANUAL, priority=0.5)
        assert wp1.id == wp2.id  # 同じインスタンスを返す
        assert len(mgr.watchpoints) == 1
        # priorityは強化されている
        assert wp1.priority > 0.5

    def test_PR_03_capacity_rejects_weak(self):
        """PR-03: 容量満杯時、新WPの優先度が既存WPより低ければ拒否"""
        mgr = WatchPointManager(max_active=2)
        # 強い既存WPを2つ配置
        wp1 = mgr.propose(target="a", trigger=WPTrigger.MANUAL, priority=0.9)
        wp2 = mgr.propose(target="b", trigger=WPTrigger.MANUAL, priority=0.9)
        # 両方を育てて強くする
        mgr.observe(wp1.id, 0.7, information_gain=0.1)
        mgr.observe(wp1.id, 0.7, information_gain=0.1)
        mgr.observe(wp1.id, 0.7, information_gain=0.1)
        mgr.observe(wp2.id, 0.7, information_gain=0.1)
        mgr.observe(wp2.id, 0.7, information_gain=0.1)
        mgr.observe(wp2.id, 0.7, information_gain=0.1)

        # 低priorityの新WPは拒否されるはず
        result = mgr.propose(target="c", trigger=WPTrigger.MANUAL, priority=0.1)
        assert result is None

    def test_PR_04_capacity_accepts_strong(self):
        """PR-04: 容量満杯でも、弱い既存WPは強い新WPに置換される"""
        mgr = WatchPointManager(max_active=2)
        wp1 = mgr.propose(target="weak", trigger=WPTrigger.MANUAL, priority=0.2)
        wp2 = mgr.propose(target="other", trigger=WPTrigger.MANUAL, priority=0.5)

        new_wp = mgr.propose(target="strong", trigger=WPTrigger.MANUAL, priority=0.95)
        assert new_wp is not None
        # 最弱のwp1がDYINGにされたはず
        assert wp1.state == WPState.DYING

    def test_PR_05_dormant_revival(self, mgr):
        """PR-05: 同じtargetが再提案されればDORMANT→ACTIVEに復活"""
        wp = mgr.propose(target="hobby", trigger=WPTrigger.MANUAL, priority=0.5)
        wp.state = WPState.DORMANT
        revived = mgr.propose(target="hobby", trigger=WPTrigger.MANUAL, priority=0.5)
        assert revived.id == wp.id
        assert wp.state == WPState.ACTIVE

    def test_PR_06_affects_dimensions_stored(self, mgr):
        """PR-06: affects_dimensionsが保存される"""
        wp = mgr.propose(
            target="career",
            trigger=WPTrigger.MANUAL,
            priority=0.6,
            affects_dimensions=["conscientiousness", "risk_tolerance"],
        )
        assert wp.affects_dimensions == ["conscientiousness", "risk_tolerance"]


# ════════════════════════════════════════════════════════════════════════════════
# OB-*: observe (観測)
# ════════════════════════════════════════════════════════════════════════════════

class TestObserve:

    def test_OB_01_records_value(self, mgr_with_wp):
        """OB-01: 観測値が記録される"""
        mgr, wp = mgr_with_wp
        ok = mgr.observe(wp.id, 0.7, information_gain=0.1)
        assert ok is True
        assert wp.observation_count == 1
        assert wp.observations == [0.7]

    def test_OB_02_hit_threshold(self, mgr_with_wp):
        """OB-02: 情報利得が閾値を超えるとhit_countが増える"""
        mgr, wp = mgr_with_wp
        mgr.observe(wp.id, 0.5, information_gain=0.005)  # 閾値未満
        assert wp.hit_count == 0
        mgr.observe(wp.id, 0.5, information_gain=0.05)  # 閾値超過
        assert wp.hit_count == 1

    def test_OB_03_unknown_id_returns_false(self, mgr):
        """OB-03: 存在しないIDに対してはFalseを返す"""
        assert mgr.observe("nonexistent_id", 0.5, 0.1) is False

    def test_OB_04_culled_rejects(self, mgr_with_wp):
        """OB-04: CULLED状態のWPには観測できない"""
        mgr, wp = mgr_with_wp
        wp.state = WPState.CULLED
        assert mgr.observe(wp.id, 0.5, 0.1) is False

    def test_OB_05_probation_graduation(self, mgr_with_wp):
        """OB-05: 試用期間中にhitがあれば卒業してACTIVEに"""
        mgr, wp = mgr_with_wp
        assert wp.state == WPState.NASCENT
        # 3回観測（probation_trials=3）すべてヒット
        mgr.observe(wp.id, 0.5, information_gain=0.1)
        mgr.observe(wp.id, 0.5, information_gain=0.1)
        mgr.observe(wp.id, 0.5, information_gain=0.1)
        assert wp.state == WPState.ACTIVE

    def test_OB_06_probation_failure(self, mgr_with_wp):
        """OB-06: 試用期間中にhitゼロならDYING"""
        mgr, wp = mgr_with_wp
        # 3回観測すべて情報利得なし
        mgr.observe(wp.id, 0.5, information_gain=0.0)
        mgr.observe(wp.id, 0.5, information_gain=0.0)
        mgr.observe(wp.id, 0.5, information_gain=0.0)
        assert wp.state == WPState.DYING
        assert wp.hit_count == 0


# ════════════════════════════════════════════════════════════════════════════════
# FT-*: fitness 計算
# ════════════════════════════════════════════════════════════════════════════════

class TestFitness:

    def test_FT_01_culled_is_zero(self, mgr_with_wp):
        """FT-01: CULLEDはfitness=0"""
        mgr, wp = mgr_with_wp
        wp.state = WPState.CULLED
        assert mgr.fitness(wp) == 0.0

    def test_FT_02_fresh_wp_has_fitness(self, mgr_with_wp):
        """FT-02: 未観測のWPも priority × 中立hit_rate × recency でfitness > 0"""
        mgr, wp = mgr_with_wp
        f = mgr.fitness(wp)
        # hit_rate=0.5 (未観測中立), info=0.1 (最低値), priority=0.7, recency≈1.0
        expected = 0.1 * 0.5 * 0.7 * 1.0
        assert f == pytest.approx(expected, abs=0.01)

    def test_FT_03_hit_boosts_fitness(self, mgr_with_wp):
        """FT-03: ヒットを重ねるとfitnessが上昇する"""
        mgr, wp = mgr_with_wp
        before = mgr.fitness(wp)
        mgr.observe(wp.id, 0.7, information_gain=0.15)
        mgr.observe(wp.id, 0.7, information_gain=0.15)
        after = mgr.fitness(wp)
        assert after > before

    def test_FT_04_no_hits_lowers_fitness(self, mgr_with_wp):
        """FT-04: 空振り観測はhit_rateを下げ、fitnessを下げる"""
        mgr, wp = mgr_with_wp
        mgr.observe(wp.id, 0.5, information_gain=0.0)
        mgr.observe(wp.id, 0.5, information_gain=0.0)
        # hit_rate=0, fitness=0 のはず
        assert mgr.fitness(wp) == 0.0

    def test_FT_05_recency_decay(self, mgr_with_wp):
        """FT-05: 古いhit_atを与えるとrecency_factorで減衰する"""
        mgr, wp = mgr_with_wp
        mgr.observe(wp.id, 0.7, information_gain=0.15)
        # 直近ヒットなら recency ≈ 1.0
        fresh = mgr.fitness(wp)
        # 30日前にセット（半減期14日 → 約0.23）
        past = datetime.now(timezone.utc) - timedelta(days=30)
        wp.last_hit_at = past.isoformat()
        old = mgr.fitness(wp)
        assert old < fresh * 0.3


# ════════════════════════════════════════════════════════════════════════════════
# EV-*: evolve (進化サイクル)
# ════════════════════════════════════════════════════════════════════════════════

class TestEvolve:

    def test_EV_01_dying_to_culled(self, mgr):
        """EV-01: DYINGは次のevolveでCULLEDになる"""
        wp = mgr.propose(target="x", trigger=WPTrigger.MANUAL, priority=0.5)
        wp.state = WPState.DYING
        stats = mgr.evolve()
        assert wp.id in stats["culled"]
        assert wp.state == WPState.CULLED

    def test_EV_02_low_fitness_becomes_dying(self, mgr):
        """EV-02: フィットネス閾値未満のACTIVEはDYINGに遷移"""
        wp = mgr.propose(target="x", trigger=WPTrigger.MANUAL, priority=0.01)
        wp.state = WPState.ACTIVE
        wp.observation_count = 10
        wp.hit_count = 0  # hit_rate=0 → fitness=0
        stats = mgr.evolve()
        assert stats["transitioned_to_dying"] >= 1
        assert wp.state in (WPState.DYING, WPState.CULLED)

    def test_EV_03_culled_archive_populated(self, mgr):
        """EV-03: CULLEDになったWPはculled_archiveに保存される"""
        wp = mgr.propose(target="x", trigger=WPTrigger.MANUAL, priority=0.5)
        wp.state = WPState.DYING
        mgr.evolve()
        assert len(mgr.culled_archive) >= 1
        assert mgr.culled_archive[-1]["id"] == wp.id

    def test_EV_04_active_wp_preserved(self, mgr):
        """EV-04: 健康なACTIVE WPはevolveで変化しない"""
        wp = mgr.propose(target="x", trigger=WPTrigger.MANUAL, priority=0.8,
                          affects_dimensions=["openness"])
        # 強く育てる
        for _ in range(3):
            mgr.observe(wp.id, 0.7, information_gain=0.15)
        assert wp.state == WPState.ACTIVE
        mgr.evolve()
        assert wp.state == WPState.ACTIVE  # 保たれる

    def test_EV_05_stats_structure(self, mgr):
        """EV-05: evolve() は正しい構造のstatsを返す"""
        stats = mgr.evolve()
        assert "transitioned_to_dormant" in stats
        assert "transitioned_to_dying" in stats
        assert "culled" in stats
        assert "merged" in stats
        assert isinstance(stats["culled"], list)


# ════════════════════════════════════════════════════════════════════════════════
# DS-*: distill_culled (SOUL蒸留)
# ════════════════════════════════════════════════════════════════════════════════

class TestDistillation:

    def test_DS_01_empty_archive(self, mgr):
        """DS-01: アーカイブが空なら0を返す"""
        soul = {"core_identity": {"openness": {"mu": 0.5, "sigma": 0.3}}}
        assert mgr.distill_culled(soul) == 0

    def test_DS_02_insufficient_hits_skipped(self, mgr):
        """DS-02: hit_count < 2 のWPは蒸留されない"""
        wp = mgr.propose(
            target="x", trigger=WPTrigger.MANUAL, priority=0.5,
            affects_dimensions=["openness"],
        )
        mgr.observe(wp.id, 0.9, information_gain=0.1)  # hit_count=1
        wp.state = WPState.DYING
        mgr.evolve()

        soul = {"core_identity": {"openness": {"mu": 0.5, "sigma": 0.3}}}
        distilled = mgr.distill_culled(soul)
        assert distilled == 0
        assert soul["core_identity"]["openness"]["mu"] == 0.5

    def test_DS_03_successful_distillation(self, mgr):
        """DS-03: 十分なhitを持つWPはSOULに蒸留される"""
        wp = mgr.propose(
            target="x", trigger=WPTrigger.MANUAL, priority=0.5,
            affects_dimensions=["openness"],
        )
        # 複数回ヒット
        for _ in range(3):
            mgr.observe(wp.id, 0.9, information_gain=0.1)
        wp.state = WPState.DYING
        mgr.evolve()

        soul = {"core_identity": {"openness": {"mu": 0.5, "sigma": 0.3}}}
        distilled = mgr.distill_culled(soul)
        assert distilled == 1
        # 観測平均0.9 の方向にμが寄る
        assert soul["core_identity"]["openness"]["mu"] > 0.5

    def test_DS_04_archive_cleared_after(self, mgr):
        """DS-04: distill後はculled_archiveがクリアされる"""
        wp = mgr.propose(
            target="x", trigger=WPTrigger.MANUAL, priority=0.5,
            affects_dimensions=["openness"],
        )
        for _ in range(3):
            mgr.observe(wp.id, 0.9, information_gain=0.1)
        wp.state = WPState.DYING
        mgr.evolve()

        soul = {"core_identity": {"openness": {"mu": 0.5, "sigma": 0.3}}}
        mgr.distill_culled(soul)
        assert mgr.culled_archive == []


# ════════════════════════════════════════════════════════════════════════════════
# RL-*: WatchPointRules
# ════════════════════════════════════════════════════════════════════════════════

class TestWatchPointRules:

    def test_RL_01_short_history_returns_empty(self):
        """RL-01: トピック履歴が短すぎれば空リストを返す"""
        rules = WatchPointRules()
        assert rules.check_topic_concentration([{"career": 1.0}]) == []

    def test_RL_02_topic_concentration_detected(self):
        """RL-02: トピック集中を検出する"""
        rules = WatchPointRules(topic_concentration_threshold=0.6)
        history = [{"career": 1.0}] * 5 + [{"sleep": 1.0}] * 2
        candidates = rules.check_topic_concentration(history)
        assert any(c["target"] == "career" for c in candidates)

    def test_RL_03_topic_below_threshold_ignored(self):
        """RL-03: 閾値未満のトピックは候補にならない"""
        rules = WatchPointRules(topic_concentration_threshold=0.6)
        history = [{"career": 0.5, "sleep": 0.5}] * 5
        candidates = rules.check_topic_concentration(history)
        assert candidates == []

    def test_RL_04_importance_spike(self):
        """RL-04: 高重要度エピソードの集中を検出"""
        rules = WatchPointRules(importance_spike_count=3, importance_spike_threshold=0.7)
        episodes = [
            {"importance": 0.8, "topic_distribution": {"career": 0.9}},
            {"importance": 0.9, "topic_distribution": {"career": 0.8}},
            {"importance": 0.75, "topic_distribution": {"career": 0.7}},
            {"importance": 0.2, "topic_distribution": {"trivia": 1.0}},
        ]
        candidates = rules.check_importance_spike(episodes)
        assert any("career" in c["target"] for c in candidates)

    def test_RL_05_personality_uncertainty(self):
        """RL-05: σ急拡大次元を検出"""
        rules = WatchPointRules(personality_sigma_spike=0.1)
        previous = {"openness": {"mu": 0.5, "sigma": 0.2}}
        current = {"openness": {"mu": 0.5, "sigma": 0.35}}  # σ +0.15
        candidates = rules.check_personality_uncertainty(current, previous)
        assert len(candidates) == 1
        assert "openness" in candidates[0]["target"]


# ════════════════════════════════════════════════════════════════════════════════
# ML-*: マージ・ライフサイクル統合
# ════════════════════════════════════════════════════════════════════════════════

class TestMergeAndLifecycle:

    def test_ML_01_similar_targets_merge(self):
        """ML-01: 類似targetを持つ2つのWPはevolveで統合される"""
        # "sleep_quality" と "sleep_pattern" のJaccard = 1/3 ≈ 0.33
        # threshold を0.3に緩めて検出させる
        mgr = WatchPointManager(max_active=10, merge_similarity_threshold=0.3)
        wp1 = mgr.propose(
            target="sleep_quality",
            trigger=WPTrigger.MANUAL,
            priority=0.9,
        )
        # 先にwp1を育ててACTIVE状態にする（merge前のDYING転落を防ぐ）
        for _ in range(3):
            mgr.observe(wp1.id, 0.7, information_gain=0.15)
        assert wp1.state == WPState.ACTIVE

        # 類似target WPをACTIVEで直接挿入
        wp2 = WatchPoint(
            id="wp_sleep_pattern_test",
            target="sleep_pattern",
            trigger=WPTrigger.MANUAL,
            priority=0.7,
            state=WPState.ACTIVE,
            created_at=_now(),
            last_hit_at=_now(),
            observation_count=5,
            hit_count=4,
            information_gain=0.12,
        )
        mgr.watchpoints[wp2.id] = wp2

        stats = mgr.evolve()
        assert len(stats["merged"]) >= 1

    def test_ML_02_full_lifecycle(self, mgr):
        """ML-02: 生成→観測→進化→蒸留の完全ライフサイクル"""
        # 1. 生成
        wp = mgr.propose(
            target="career",
            trigger=WPTrigger.TOPIC_CONCENTRATION,
            priority=0.7,
            affects_dimensions=["conscientiousness"],
        )
        assert wp.state == WPState.NASCENT

        # 2. 観測（試用期間クリア）
        for _ in range(3):
            mgr.observe(wp.id, 0.8, information_gain=0.12)
        assert wp.state == WPState.ACTIVE

        # 3. 長期非アクティブ化 → DYING 条件を満たすようfitnessを落とす
        wp.priority = 0.0  # fitnessをゼロにする
        stats = mgr.evolve()
        assert wp.state in (WPState.DYING, WPState.CULLED)

        # 4. 次のevolveでCULLED
        if wp.state == WPState.DYING:
            mgr.evolve()
        assert wp.state == WPState.CULLED

        # 5. 蒸留
        soul = {"core_identity": {"conscientiousness": {"mu": 0.5, "sigma": 0.3}}}
        distilled = mgr.distill_culled(soul)
        assert distilled >= 1
        # SOUL coreのμが観測値0.8方向に動いた
        assert soul["core_identity"]["conscientiousness"]["mu"] > 0.5


# ════════════════════════════════════════════════════════════════════════════════
# Entry point
# ════════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
