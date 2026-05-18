#!/usr/bin/env python3
"""
Ghost-Printer — Soul Cortex テストスイート
3モデル協調アーキテクチャの統合テスト

テスト項目:
  1. 各モデルの独立観測
  2. モデル間情報交換（Whisper→Bonsai, Bonsai→MiniLM, MiniLM→Bonsai）
  3. ベイズ統合
  4. Shared State の一貫性
  5. E2Eパイプライン
"""

import sys
import os
import math
import random

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from soul_schema import create_empty_soul
from soul_cortex import (
    SoulCortex,
    SharedSoulState,
    AcousticObservation,
    SemanticObservation,
    EmbeddingObservation,
    _cosine_similarity,
    _estimate_valence,
    _estimate_arousal,
)


# ════════════════════════════════════════════════════════════════════════════════
# ヘルパー
# ════════════════════════════════════════════════════════════════════════════════

def make_cortex():
    soul = create_empty_soul("test_user")
    return SoulCortex(soul), soul

def make_audio(excited=True):
    if excited:
        return {"pitch_mean": 0.7, "pitch_variance": 0.35, "tempo": 0.75,
                "pause_ratio": 0.08, "energy_mean": 0.72, "energy_variance": 0.3,
                "duration": 40.0, "speech_ratio": 0.88}
    else:
        return {"pitch_mean": 0.3, "pitch_variance": 0.05, "tempo": 0.2,
                "pause_ratio": 0.55, "energy_mean": 0.2, "energy_variance": 0.03,
                "duration": 8.0, "speech_ratio": 0.25}

def make_text_features(positive=True):
    if positive:
        return {
            "personality_signals": [
                {"dimension": "openness", "value": 0.85, "confidence": 0.6},
                {"dimension": "curiosity", "value": 0.90, "confidence": 0.7},
            ],
            "emotion_distribution": {"excitement": 0.7, "joy": 0.2, "neutral": 0.1},
            "importance": 0.8, "importance_confidence": 0.6,
            "topic_distribution": {"tech": 0.6, "innovation": 0.4},
            "value_signals": {"creativity": 0.7},
        }
    else:
        return {
            "personality_signals": [
                {"dimension": "extraversion", "value": 0.2, "confidence": 0.5},
                {"dimension": "neuroticism", "value": 0.6, "confidence": 0.4},
            ],
            "emotion_distribution": {"sadness": 0.5, "anxiety": 0.3, "neutral": 0.2},
            "importance": 0.4, "importance_confidence": 0.5,
            "topic_distribution": {"health": 0.5, "stress": 0.5},
            "value_signals": {"safety": 0.6},
        }

def make_embedding(seed=42):
    random.seed(seed)
    return [random.gauss(0, 1) for _ in range(512)]


# ════════════════════════════════════════════════════════════════════════════════
# 1. 音響層テスト
# ════════════════════════════════════════════════════════════════════════════════

def test_acoustic_excited():
    """興奮した音声 → 高valence, 高arousal"""
    cortex, _ = make_cortex()
    obs = cortex.process_acoustic(make_audio(excited=True))
    assert obs.acoustic_valence > 0.6, f"Excited valence should be >0.6, got {obs.acoustic_valence}"
    assert obs.acoustic_arousal > 0.5, f"Excited arousal should be >0.5, got {obs.acoustic_arousal}"
    assert obs.acoustic_confidence > 0.5, f"Long speech should have high confidence"
    return True

def test_acoustic_calm():
    """静かな音声 → 低arousal"""
    cortex, _ = make_cortex()
    obs = cortex.process_acoustic(make_audio(excited=False))
    assert obs.acoustic_arousal < 0.4, f"Calm arousal should be <0.4, got {obs.acoustic_arousal}"
    assert obs.acoustic_confidence < 0.5, f"Short speech should have lower confidence"
    return True

def test_acoustic_written_to_shared():
    """音響観測がShared Stateに書き込まれる"""
    cortex, _ = make_cortex()
    cortex.process_acoustic(make_audio())
    assert cortex.shared_state.stats()["acoustic_observations"] == 1
    latest = cortex.shared_state.read_latest_acoustic()
    assert latest is not None
    return True


# ════════════════════════════════════════════════════════════════════════════════
# 2. 意味層テスト
# ════════════════════════════════════════════════════════════════════════════════

def test_semantic_basic():
    """基本的な意味抽出"""
    cortex, _ = make_cortex()
    obs = cortex.process_semantic(make_text_features(positive=True))
    assert obs.importance == 0.8
    assert len(obs.personality_signals) == 2
    assert obs.emotion_distribution.get("excitement", 0) > 0
    return True

def test_semantic_acoustic_boost():
    """Whisper→Bonsai: 一致する音響証拠で確信度がブーストされる"""
    cortex, _ = make_cortex()
    # まず音響（興奮的）を処理
    a_obs = cortex.process_acoustic(make_audio(excited=True))
    # 次にポジティブな意味特徴を処理（音響と一致 → ブーストされるはず）
    text_feat = make_text_features(positive=True)
    original_conf = text_feat["personality_signals"][0]["confidence"]
    s_obs = cortex.process_semantic(text_feat, a_obs)
    boosted_conf = s_obs.personality_signals[0]["confidence"]
    assert s_obs.acoustic_boost > 0, f"Matching emotion should boost, got {s_obs.acoustic_boost}"
    assert boosted_conf > original_conf, \
        f"Confidence should increase: {original_conf} → {boosted_conf}"
    return True

def test_semantic_acoustic_mismatch():
    """Whisper→Bonsai: 矛盾する音響証拠で確信度が下がる"""
    cortex, _ = make_cortex()
    # 静かな音声（低arousal, 低valence）
    a_obs = cortex.process_acoustic(make_audio(excited=False))
    # しかしBonsaiはポジティブな感情を検出（矛盾）
    text_feat = make_text_features(positive=True)
    s_obs = cortex.process_semantic(text_feat, a_obs)
    # 矛盾 → ブーストが低いか負
    # 静かな音声のconfidenceが低いので大きなペナルティにはならない
    assert s_obs.acoustic_boost <= 0.1, \
        f"Mismatched emotion should have low/negative boost, got {s_obs.acoustic_boost}"
    return True


# ════════════════════════════════════════════════════════════════════════════════
# 3. 埋込層テスト
# ════════════════════════════════════════════════════════════════════════════════

def test_embedding_basic():
    """基本的な埋め込み観測"""
    cortex, _ = make_cortex()
    emb = make_embedding(42)
    obs = cortex.process_embedding("テスト", emb)
    assert len(obs.vector) == 512
    assert obs.weighted_importance == 1.0  # Bonsaiの観測がまだない
    return True

def test_embedding_importance_from_bonsai():
    """Bonsai→MiniLM: 重要度がembeddingの重みに影響"""
    cortex, _ = make_cortex()
    # まずBonsaiで高重要度を設定
    cortex.process_semantic(make_text_features(positive=True))
    # 次にMiniLMが処理 → 重要度が反映される
    obs = cortex.process_embedding("テスト", make_embedding(42))
    assert obs.weighted_importance == 0.8, \
        f"Should reflect Bonsai importance 0.8, got {obs.weighted_importance}"
    return True

def test_embedding_similarity_search():
    """類似エピソード検索"""
    cortex, _ = make_cortex()
    # 3つの埋め込みを蓄積
    for seed in [10, 20, 30]:
        cortex.process_embedding(f"text_{seed}", make_embedding(seed))
    # 同じseed=10のベクトルで検索 → 完全一致が見つかるはず
    obs = cortex.process_embedding("query", make_embedding(10))
    assert len(obs.nearest_episodes) > 0
    # 最も類似するのはseed=10のベクトル（自分自身を除くとindex=0）
    top_sim = obs.nearest_episodes[0]["similarity"]
    assert top_sim > 0.99, f"Same vector should have similarity ~1.0, got {top_sim}"
    return True


# ════════════════════════════════════════════════════════════════════════════════
# 4. MiniLM→Bonsai 情報交換テスト
# ════════════════════════════════════════════════════════════════════════════════

def test_context_boost_from_embedding():
    """MiniLM→Bonsai: 類似エピソードの存在がBonsaiの確信度を上げる"""
    cortex, _ = make_cortex()
    # 類似パターンを蓄積（同じ埋め込みを3回）
    for _ in range(3):
        emb = make_embedding(42)
        obs = EmbeddingObservation(
            vector=emb,
            nearest_episodes=[{"index": 0, "similarity": 0.95}] * 3,
        )
        cortex.shared_state.write_embedding(obs)

    # Bonsaiが処理 → 類似エピソードの存在でブースト
    text_feat = make_text_features(positive=True)
    original_conf = text_feat["personality_signals"][0]["confidence"]
    s_obs = cortex.process_semantic(text_feat)
    new_conf = s_obs.personality_signals[0]["confidence"]
    assert new_conf >= original_conf, \
        f"Context boost should increase confidence: {original_conf} → {new_conf}"
    return True


# ════════════════════════════════════════════════════════════════════════════════
# 5. ベイズ統合テスト
# ════════════════════════════════════════════════════════════════════════════════

def test_integration_updates_soul():
    """統合でSOULのcore_identityが更新される"""
    cortex, soul = make_cortex()
    original_mu = soul["core_identity"]["openness"]["mu"]  # 0.5

    a_obs = cortex.process_acoustic(make_audio(excited=True))
    s_obs = cortex.process_semantic(make_text_features(positive=True), a_obs)
    e_obs = cortex.process_embedding("テスト", make_embedding())

    cortex.integrate_observations(a_obs, s_obs, e_obs)

    new_mu = soul["core_identity"]["openness"]["mu"]
    assert new_mu > original_mu, \
        f"Openness should increase from {original_mu}, got {new_mu}"
    return True

def test_integration_adds_episode():
    """統合でエピソードが追加される"""
    cortex, soul = make_cortex()
    assert len(soul["episodic_memory"]["recent"]) == 0

    s_obs = SemanticObservation(importance=0.7, personality_signals=[])
    cortex.integrate_observations(None, s_obs, None)

    assert len(soul["episodic_memory"]["recent"]) == 1
    ep = soul["episodic_memory"]["recent"][0]
    assert ep["importance"] == 0.7
    return True

def test_integration_with_acoustic_boost():
    """音響ブースト付き統合でσがより速く収束する"""
    # 音響なし
    cortex1, soul1 = make_cortex()
    s1 = cortex1.process_semantic(make_text_features(positive=True))
    cortex1.integrate_observations(None, s1, None)
    sigma1 = soul1["core_identity"]["openness"]["sigma"]

    # 音響あり（一致）
    cortex2, soul2 = make_cortex()
    a2 = cortex2.process_acoustic(make_audio(excited=True))
    s2 = cortex2.process_semantic(make_text_features(positive=True), a2)
    cortex2.integrate_observations(a2, s2, None)
    sigma2 = soul2["core_identity"]["openness"]["sigma"]

    assert sigma2 <= sigma1, \
        f"Acoustic boost should converge faster: σ_no_audio={sigma1:.4f} >= σ_with_audio={sigma2:.4f}"
    return True

def test_integration_updates_semantic_map():
    """統合でsemantic_mapが更新される"""
    cortex, soul = make_cortex()
    s_obs = cortex.process_semantic(make_text_features(positive=True))
    cortex.integrate_observations(None, s_obs, None)
    assert "tech" in soul["semantic_map"]["interests"]
    assert "creativity" in soul["semantic_map"]["values"]
    return True


# ════════════════════════════════════════════════════════════════════════════════
# 6. Shared Stateテスト
# ════════════════════════════════════════════════════════════════════════════════

def test_shared_state_history():
    """Shared Stateが履歴を正しく保持"""
    cortex, _ = make_cortex()
    for i in range(5):
        cortex.process_acoustic(make_audio(i % 2 == 0))
    stats = cortex.shared_state.stats()
    assert stats["acoustic_observations"] == 5
    return True

def test_shared_state_trend():
    """音響トレンドの計算"""
    cortex, _ = make_cortex()
    for _ in range(3):
        cortex.process_acoustic(make_audio(excited=True))
    for _ in range(2):
        cortex.process_acoustic(make_audio(excited=False))
    trend = cortex.shared_state.read_acoustic_trend(5)
    assert "avg_valence" in trend
    assert trend["trend_length"] == 5
    return True


# ════════════════════════════════════════════════════════════════════════════════
# 7. E2Eパイプラインテスト
# ════════════════════════════════════════════════════════════════════════════════

def test_e2e_full_pipeline():
    """3モデル全てを使ったフルパイプライン"""
    cortex, soul = make_cortex()
    updated = cortex.process(
        text="新しいプロジェクトに興奮している",
        audio_features=make_audio(excited=True),
        text_features=make_text_features(positive=True),
        embedding_vector=make_embedding(42),
    )
    assert len(updated["episodic_memory"]["recent"]) == 1
    assert updated["stats"]["total_episodes"] == 1
    stats = cortex.shared_state.stats()
    assert stats["acoustic_observations"] == 1
    assert stats["semantic_observations"] == 1
    assert stats["embedding_observations"] == 1
    return True

def test_e2e_text_only():
    """テキストのみ（音声なし・埋め込みなし）"""
    cortex, soul = make_cortex()
    updated = cortex.process(
        text="テキストだけ",
        text_features=make_text_features(positive=True),
    )
    assert len(updated["episodic_memory"]["recent"]) == 1
    stats = cortex.shared_state.stats()
    assert stats["acoustic_observations"] == 0
    assert stats["semantic_observations"] == 1
    return True

def test_e2e_multiple_inputs():
    """複数回の入力でSOULが蓄積される"""
    cortex, soul = make_cortex()
    for i in range(5):
        positive = (i % 2 == 0)
        cortex.process(
            text=f"input_{i}",
            audio_features=make_audio(excited=positive),
            text_features=make_text_features(positive=positive),
            embedding_vector=make_embedding(i),
        )
    assert soul["stats"]["total_episodes"] == 5
    assert len(soul["episodic_memory"]["recent"]) == 5
    stats = cortex.shared_state.stats()
    assert stats["acoustic_observations"] == 5
    assert stats["semantic_observations"] == 5
    assert stats["embedding_observations"] == 5
    return True


# ════════════════════════════════════════════════════════════════════════════════
# 8. ユーティリティテスト
# ════════════════════════════════════════════════════════════════════════════════

def test_cosine_similarity():
    """コサイン類似度"""
    assert abs(_cosine_similarity([1, 0], [1, 0]) - 1.0) < 0.001
    assert abs(_cosine_similarity([1, 0], [0, 1]) - 0.0) < 0.001
    assert abs(_cosine_similarity([1, 0], [-1, 0]) - (-1.0)) < 0.001
    assert _cosine_similarity([], []) == 0.0
    return True


# ════════════════════════════════════════════════════════════════════════════════
# CORTEX統合テスト
# ════════════════════════════════════════════════════════════════════════════════

def test_cortex_integration_default():
    """CX-01: CORTEX未接続時はデフォルトパラメータで動作する"""
    cortex_sc, soul = make_cortex()
    assert cortex_sc._acoustic_boost_weight == 0.2
    assert cortex_sc._acoustic_mismatch_penalty == 0.15
    assert cortex_sc._bayesian_sigma_floor == 0.02
    return True


def test_cortex_integration_custom_params():
    """CX-02: CORTEXから読み込んだパラメータが適用される"""
    from cortex_manager import CortexManager
    mgr = CortexManager()
    cortex_obj = mgr.build()
    # カスタムパラメータ
    mgr.update_param("cortex.acoustic_boost_weight", 0.35)
    mgr.update_param("cortex.acoustic_mismatch_penalty", 0.25)
    mgr.update_param("cortex.bayesian_sigma_floor", 0.05)

    soul = create_empty_soul("test_user")
    sc = SoulCortex(soul, cortex_obj)
    assert sc._acoustic_boost_weight == 0.35
    assert sc._acoustic_mismatch_penalty == 0.25
    assert sc._bayesian_sigma_floor == 0.05
    return True


def test_cortex_from_file():
    """CX-03: CORTEX.binファイルからSoulCortexを初期化できる"""
    import tempfile, os
    from cortex_manager import CortexManager
    mgr = CortexManager()
    mgr.build()
    mgr.update_param("cortex.acoustic_boost_weight", 0.4)

    with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
        tmp_path = f.name
    try:
        mgr.save(tmp_path)
        soul = create_empty_soul("test_user")
        sc = SoulCortex.from_cortex_file(soul, tmp_path)
        assert sc._acoustic_boost_weight == 0.4
        assert sc.cortex is not None
    finally:
        os.unlink(tmp_path)
    return True


def test_cortex_params_affect_processing():
    """CX-04: CORTEXパラメータが実際の処理に影響する"""
    from cortex_manager import CortexManager

    soul1 = create_empty_soul("test_user")
    sc1 = SoulCortex(soul1)  # デフォルト

    mgr = CortexManager()
    cortex_obj = mgr.build()
    mgr.update_param("cortex.acoustic_boost_weight", 0.5)  # 2.5倍に増加
    soul2 = create_empty_soul("test_user")
    sc2 = SoulCortex(soul2, cortex_obj)

    # 同じ入力を処理
    audio = make_audio(excited=True)
    text_feat = make_text_features(positive=True)

    a1 = sc1.process_acoustic(audio)
    s1 = sc1.process_semantic(text_feat, a1)

    a2 = sc2.process_acoustic(audio)
    s2 = sc2.process_semantic(text_feat, a2)

    # acoustic_boost_weight が大きい方がブーストも大きい
    assert abs(s2.acoustic_boost) > abs(s1.acoustic_boost)
    return True


def test_cortex_bayesian_sigma_floor():
    """CX-05: bayesian_sigma_floorがベイズ更新に反映される"""
    from soul_cortex import bayesian_update_gaussian

    # デフォルト floor=0.02
    _, sigma_default = bayesian_update_gaussian(0.5, 0.03, 0.5, 0.99)
    assert sigma_default >= 0.02

    # floor=0.10に上げる
    _, sigma_high = bayesian_update_gaussian(0.5, 0.03, 0.5, 0.99, sigma_floor=0.10)
    assert sigma_high >= 0.10
    assert sigma_high > sigma_default
    return True


# ════════════════════════════════════════════════════════════════════════════════
# WP-*: Watch Point 統合テスト
# ════════════════════════════════════════════════════════════════════════════════

def test_wp_integration_manager_initialized():
    """WP-01: SoulCortex起動時にWatchPointManagerが初期化される"""
    cortex, soul = make_cortex()
    assert cortex.wp_manager is not None
    assert cortex.wp_rules is not None
    assert cortex.wp_manager.count()["nascent"] == 0
    return True


def test_wp_integration_topic_concentration_triggers():
    """WP-02: トピック集中が続けばWPが自動提案される"""
    cortex, soul = make_cortex()

    # 同じトピックが頻出する観測を繰り返す
    for _ in range(5):
        obs = SemanticObservation(
            importance=0.6,
            emotion_distribution={"joy": 0.5},
            personality_signals=[],
            topic_distribution={"career": 1.0},
            timestamp="2026-04-16T12:00:00+00:00",
        )
        cortex.integrate_observations(None, obs, None)

    # career WPが自動生成されたはず
    wp_targets = [wp.target for wp in cortex.wp_manager.active()]
    assert "career" in wp_targets
    return True


def test_wp_integration_observation_recorded():
    """WP-03: 既存WPが新観測を受け取ってhit_countを更新する"""
    cortex, soul = make_cortex()

    # 手動でWPを追加
    wp = cortex.wp_manager.propose(
        target="career",
        trigger=None,  # 一旦 None で、次の行で差し替え
        priority=0.8,
    )
    # trigger引数は Enum 必須なので修正
    from watchpoint import WPTrigger
    wp.trigger = WPTrigger.MANUAL

    before_obs = wp.observation_count

    # 関連するトピックを持つ観測を送る
    obs = SemanticObservation(
        importance=0.8,
        emotion_distribution={"joy": 0.5},
        personality_signals=[],
        topic_distribution={"career": 0.9},
        timestamp="2026-04-16T12:00:00+00:00",
    )
    cortex.integrate_observations(None, obs, None)

    assert wp.observation_count > before_obs
    return True


def test_wp_integration_evolve_and_distill():
    """WP-04: evolve_watchpoints() が進化＆蒸留を実行する"""
    cortex, soul = make_cortex()

    # WPを1つ追加し、育てる
    from watchpoint import WPTrigger, WPState
    wp = cortex.wp_manager.propose(
        target="openness",
        trigger=WPTrigger.MANUAL,
        priority=0.8,
        affects_dimensions=["openness"],
    )
    # 3回観測してACTIVEに昇格
    for _ in range(3):
        cortex.wp_manager.observe(wp.id, 0.9, information_gain=0.1)

    # 強制的にDYINGにする
    wp.state = WPState.DYING
    before_mu = soul["core_identity"]["openness"]["mu"]

    # evolve() - DYING → CULLED、蒸留
    stats = cortex.evolve_watchpoints()

    assert stats is not None
    assert wp.id in stats["culled"]
    # 観測平均0.9方向にμが動いているはず
    assert soul["core_identity"]["openness"]["mu"] > before_mu
    # SOULにwatchpointsリストが書き戻されている
    assert "watchpoints" in soul
    return True


def test_wp_integration_cortex_params_propagate():
    """WP-05: CORTEX.watchpointのパラメータがWatchPointManagerに反映される"""
    from cortex_manager import CortexManager

    # 非デフォルト値でCORTEXを構築
    mgr = CortexManager()
    c = mgr.build()
    c.watchpoint.max_active = 42
    c.watchpoint.fitness_floor = 0.15
    c.watchpoint.topic_concentration_threshold = 0.8

    soul = create_empty_soul("test_user")
    cortex = SoulCortex(soul, cortex=c)
    assert cortex.wp_manager.max_active == 42
    assert cortex.wp_manager.fitness_floor == 0.15
    assert cortex.wp_rules.topic_threshold == 0.8
    return True


def test_wp_integration_persists_to_soul():
    """WP-06: WPはSOULに書き戻される"""
    cortex, soul = make_cortex()

    from watchpoint import WPTrigger
    wp = cortex.wp_manager.propose(
        target="persistent_wp",
        trigger=WPTrigger.MANUAL,
        priority=0.5,
    )
    cortex.evolve_watchpoints()  # to_list() 実行

    assert "watchpoints" in soul
    assert len(soul["watchpoints"]) >= 1
    targets = [d["target"] for d in soul["watchpoints"]]
    assert "persistent_wp" in targets
    return True


def test_wp_integration_restore_from_soul():
    """WP-07: SOULに保存されたWPを復元できる"""
    from watchpoint import WPTrigger

    cortex1, soul1 = make_cortex()
    wp = cortex1.wp_manager.propose(
        target="restore_test",
        trigger=WPTrigger.MANUAL,
        priority=0.7,
    )
    cortex1.evolve_watchpoints()  # SOULに保存

    # 同じSOULで新しいSoulCortexを初期化 → WPが復元されるはず
    cortex2 = SoulCortex(soul1)
    targets = [wp.target for wp in cortex2.wp_manager.watchpoints.values()]
    assert "restore_test" in targets
    return True


# ════════════════════════════════════════════════════════════════════════════════
# ランナー
# ════════════════════════════════════════════════════════════════════════════════

def run_all_tests():
    tests = [
        ("AC-01: Excited audio → high valence/arousal",   test_acoustic_excited),
        ("AC-02: Calm audio → low arousal",               test_acoustic_calm),
        ("AC-03: Acoustic written to shared state",        test_acoustic_written_to_shared),
        ("SM-01: Basic semantic extraction",               test_semantic_basic),
        ("SM-02: Whisper→Bonsai acoustic boost",           test_semantic_acoustic_boost),
        ("SM-03: Whisper→Bonsai mismatch penalty",         test_semantic_acoustic_mismatch),
        ("EM-01: Basic embedding observation",             test_embedding_basic),
        ("EM-02: Bonsai→MiniLM importance transfer",       test_embedding_importance_from_bonsai),
        ("EM-03: Similarity search",                       test_embedding_similarity_search),
        ("EX-01: MiniLM→Bonsai context boost",             test_context_boost_from_embedding),
        ("INT-01: Integration updates SOUL",               test_integration_updates_soul),
        ("INT-02: Integration adds episode",               test_integration_adds_episode),
        ("INT-03: Acoustic boost → faster convergence",    test_integration_with_acoustic_boost),
        ("INT-04: Integration updates semantic map",       test_integration_updates_semantic_map),
        ("SS-01: Shared state history",                    test_shared_state_history),
        ("SS-02: Acoustic trend calculation",              test_shared_state_trend),
        ("E2E-01: Full 3-model pipeline",                  test_e2e_full_pipeline),
        ("E2E-02: Text-only pipeline",                     test_e2e_text_only),
        ("E2E-03: Multiple inputs accumulation",           test_e2e_multiple_inputs),
        ("UTL-01: Cosine similarity",                      test_cosine_similarity),
        ("CX-01: Default params without CORTEX",            test_cortex_integration_default),
        ("CX-02: Custom params from CORTEX",                test_cortex_integration_custom_params),
        ("CX-03: Load from CORTEX.bin file",                test_cortex_from_file),
        ("CX-04: CORTEX params affect processing",          test_cortex_params_affect_processing),
        ("CX-05: Bayesian sigma floor from CORTEX",         test_cortex_bayesian_sigma_floor),
        ("WP-01: WatchPointManager initialized",            test_wp_integration_manager_initialized),
        ("WP-02: Topic concentration triggers WP",          test_wp_integration_topic_concentration_triggers),
        ("WP-03: Observation recorded on existing WP",       test_wp_integration_observation_recorded),
        ("WP-04: evolve_watchpoints executes distillation", test_wp_integration_evolve_and_distill),
        ("WP-05: CORTEX WP params propagate",               test_wp_integration_cortex_params_propagate),
        ("WP-06: WPs persist to SOUL",                      test_wp_integration_persists_to_soul),
        ("WP-07: WPs restore from SOUL",                    test_wp_integration_restore_from_soul),
    ]

    print("═══ Ghost-Printer Soul Cortex Test Suite ═══\n")

    passed = 0
    failed = 0
    errors = []

    for name, fn in tests:
        try:
            if fn():
                print(f"  ✅ {name}")
                passed += 1
            else:
                print(f"  ❌ {name} — returned False")
                failed += 1
                errors.append((name, "False"))
        except Exception as e:
            print(f"  ❌ {name} — {e}")
            failed += 1
            errors.append((name, str(e)))

    print(f"\n═══ Results: {passed}/{passed + failed} passed ═══")
    if errors:
        print("\nFailures:")
        for name, err in errors:
            print(f"  - {name}: {err}")

    return passed, failed


if __name__ == "__main__":
    passed, failed = run_all_tests()
    sys.exit(0 if failed == 0 else 1)
