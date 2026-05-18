"""
Ghost-Printer — Soul Cortex: 3モデル協調アーキテクチャ

設計思想:
  従来の直列パイプライン (Whisper → Bonsai → MiniLM → SOUL) を脱却し、
  3つのモデルがそれぞれ独自の「観測層」を持ち、Shared Soul State を
  介して互いの観測を参照・補強し合う協調システム。

  各モデルの役割:
    Whisper (音響層)  — 韻律・ピッチ・テンポ・ポーズから感情の音響証拠を蓄積
    Bonsai  (意味層)  — テキスト+音響特徴+類似度から性格・感情・重要度を抽出
    MiniLM  (埋込層)  — 512次元ベクトル空間でトピック・エピソード間関係を蓄積

  重要: SOULは確率分布とベクトルで格納する。テキストは格納しない。
         外部AI向けのテキスト変換はPermission Gatewayの責務。

3モデルの情報交換プロトコル:
  1. Whisper → Bonsai: 音響感情特徴 (prosody_features)
     話速の変化やピッチの揺れが、感情抽出の確信度を補強する
  2. Bonsai → MiniLM: 重要度スコア (importance)
     重要なエピソードの埋め込みにより大きな重みを付与する
  3. MiniLM → Bonsai: 意味的近接性 (semantic_context)
     過去エピソードとの類似度が、文脈理解を補強する
"""

import math
import json
import hashlib
import numpy as np
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List, Any, Tuple
from pathlib import Path

# CORTEX統合: 設定をCORTEXから読み込み可能にする
try:
    from cortex_manager import (
        CortexManager, Cortex, CortexConfig, WhisperConfig, WatchPointConfig,
    )
except ImportError:
    CortexManager = None  # type: ignore
    WatchPointConfig = None  # type: ignore

# Watch Point生態系（optional: watchpoint.pyが存在すれば有効化）
try:
    from watchpoint import (
        WatchPointManager, WatchPointRules, WPTrigger, WPState,
    )
except ImportError:
    WatchPointManager = None  # type: ignore
    WatchPointRules = None  # type: ignore


# ════════════════════════════════════════════════════════════════════════════════
# 1. 各モデルの観測データ構造（テキストではなく数値表現）
# ════════════════════════════════════════════════════════════════════════════════

@dataclass
class AcousticObservation:
    """Whisperの音響層が蓄積する観測データ"""

    # 韻律特徴ベクトル (正規化済み)
    pitch_mean: float = 0.0          # 平均ピッチ (正規化 0-1)
    pitch_variance: float = 0.0      # ピッチ変動 (高い = 感情的)
    tempo: float = 0.0               # 話速 (正規化 0-1)
    pause_ratio: float = 0.0         # ポーズ比率 (全体に占める沈黙の割合)
    energy_mean: float = 0.0         # 平均エネルギー (音量)
    energy_variance: float = 0.0     # エネルギー変動

    # 音響から推定される感情ベクトル
    acoustic_valence: float = 0.5    # 快-不快 (0=不快, 1=快)
    acoustic_arousal: float = 0.5    # 覚醒度 (0=低, 1=高)
    acoustic_confidence: float = 0.3 # この推定の確信度

    # 発話区間のメタデータ
    duration_seconds: float = 0.0
    speech_ratio: float = 0.0       # 発話区間 / 全体時間

    timestamp: str = ""

    def to_vector(self) -> list:
        """固定長ベクトルとして返す（10次元）"""
        return [
            self.pitch_mean, self.pitch_variance,
            self.tempo, self.pause_ratio,
            self.energy_mean, self.energy_variance,
            self.acoustic_valence, self.acoustic_arousal,
            self.duration_seconds / 300.0,  # 5分で正規化
            self.speech_ratio,
        ]

    def to_dict(self) -> dict:
        return {
            "pitch": {"mean": self.pitch_mean, "variance": self.pitch_variance},
            "tempo": self.tempo,
            "pause_ratio": self.pause_ratio,
            "energy": {"mean": self.energy_mean, "variance": self.energy_variance},
            "emotion": {
                "valence": self.acoustic_valence,
                "arousal": self.acoustic_arousal,
                "confidence": self.acoustic_confidence,
            },
            "duration": self.duration_seconds,
            "speech_ratio": self.speech_ratio,
            "timestamp": self.timestamp,
        }


@dataclass
class SemanticObservation:
    """Bonsaiの意味層が蓄積する観測データ"""

    # 性格シグナル: [{dimension, value, confidence}]
    personality_signals: List[Dict[str, float]] = field(default_factory=list)

    # 感情 (カテゴリ確率分布)
    emotion_distribution: Dict[str, float] = field(default_factory=dict)
    # 例: {"joy": 0.6, "curiosity": 0.3, "neutral": 0.1}

    # 重要度 (0-1, ベイズ推定)
    importance: float = 0.0
    importance_confidence: float = 0.3

    # トピック分布 (Dirichlet的)
    topic_distribution: Dict[str, float] = field(default_factory=dict)

    # 価値観シグナル
    value_signals: Dict[str, float] = field(default_factory=dict)

    # 音響層からの補強を受けた確信度調整
    acoustic_boost: float = 0.0  # 音響証拠による確信度のブースト量

    timestamp: str = ""

    def to_dict(self) -> dict:
        return {
            "personality_signals": self.personality_signals,
            "emotion_distribution": self.emotion_distribution,
            "importance": self.importance,
            "importance_confidence": self.importance_confidence,
            "topic_distribution": self.topic_distribution,
            "value_signals": self.value_signals,
            "acoustic_boost": self.acoustic_boost,
            "timestamp": self.timestamp,
        }


@dataclass
class EmbeddingObservation:
    """MiniLMの埋込層が蓄積する観測データ"""

    # 512次元の埋め込みベクトル
    vector: List[float] = field(default_factory=list)

    # 過去エピソードとの類似度 (上位K件)
    nearest_episodes: List[Dict[str, float]] = field(default_factory=list)
    # [{"episode_id": str, "similarity": float}]

    # トピッククラスタへの所属確率
    cluster_assignment: Dict[str, float] = field(default_factory=dict)
    # {"cluster_0": 0.7, "cluster_1": 0.2, "cluster_2": 0.1}

    # 重要度で重み付けされたベクトル (Bonsaiからの情報交換)
    weighted_importance: float = 1.0

    timestamp: str = ""

    def to_dict(self) -> dict:
        return {
            "vector_dim": len(self.vector),
            "vector_hash": hashlib.sha256(
                json.dumps(self.vector[:8]).encode()
            ).hexdigest()[:12] if self.vector else "",
            "nearest_episodes": self.nearest_episodes[:5],
            "cluster_assignment": self.cluster_assignment,
            "weighted_importance": self.weighted_importance,
            "timestamp": self.timestamp,
        }


# ════════════════════════════════════════════════════════════════════════════════
# 2. Shared Soul State（3モデルが読み書きする共有状態）
# ════════════════════════════════════════════════════════════════════════════════

class SharedSoulState:
    """
    3つのモデルが共有するSOUL状態。

    各モデルは自分の観測を書き込み、他モデルの観測を読み取る。
    これがモデル間の「情報交換」を実現する。
    """

    def __init__(self, soul: dict):
        self.soul = soul
        self._acoustic_history: List[AcousticObservation] = []
        self._semantic_history: List[SemanticObservation] = []
        self._embedding_history: List[EmbeddingObservation] = []
        self._embedding_matrix: List[List[float]] = []  # 全エピソードの埋め込み

    # ── 書き込み ──

    def write_acoustic(self, obs: AcousticObservation) -> None:
        """Whisperが音響観測を書き込む"""
        self._acoustic_history.append(obs)
        # 最新100件を保持
        if len(self._acoustic_history) > 100:
            self._acoustic_history = self._acoustic_history[-100:]

    def write_semantic(self, obs: SemanticObservation) -> None:
        """Bonsaiが意味観測を書き込む"""
        self._semantic_history.append(obs)
        if len(self._semantic_history) > 100:
            self._semantic_history = self._semantic_history[-100:]

    def write_embedding(self, obs: EmbeddingObservation) -> None:
        """MiniLMが埋め込み観測を書き込む"""
        self._embedding_history.append(obs)
        if obs.vector:
            self._embedding_matrix.append(obs.vector)
        if len(self._embedding_history) > 100:
            self._embedding_history = self._embedding_history[-100:]
            self._embedding_matrix = self._embedding_matrix[-100:]

    # ── 読み取り（モデル間情報交換） ──

    def read_latest_acoustic(self) -> Optional[AcousticObservation]:
        """Bonsaiが音響層の最新観測を読む"""
        return self._acoustic_history[-1] if self._acoustic_history else None

    def read_acoustic_trend(self, n: int = 10) -> Dict[str, float]:
        """直近n件の音響トレンド（平均値）"""
        if not self._acoustic_history:
            return {}
        recent = self._acoustic_history[-n:]
        return {
            "avg_pitch_variance": sum(a.pitch_variance for a in recent) / len(recent),
            "avg_tempo": sum(a.tempo for a in recent) / len(recent),
            "avg_valence": sum(a.acoustic_valence for a in recent) / len(recent),
            "avg_arousal": sum(a.acoustic_arousal for a in recent) / len(recent),
            "trend_length": len(recent),
        }

    def read_latest_semantic(self) -> Optional[SemanticObservation]:
        """MiniLMがBonsaiの最新観測を読む"""
        return self._semantic_history[-1] if self._semantic_history else None

    def read_importance_history(self, n: int = 10) -> List[float]:
        """直近n件の重要度履歴"""
        return [s.importance for s in self._semantic_history[-n:]]

    def read_latest_embedding(self) -> Optional[EmbeddingObservation]:
        """Bonsaiが埋め込み層の最新観測を読む"""
        return self._embedding_history[-1] if self._embedding_history else None

    def find_similar_episodes(self, vector: List[float], top_k: int = 5) -> List[Dict]:
        """ベクトル空間で類似エピソードを検索"""
        if not self._embedding_matrix or not vector:
            return []

        similarities = []
        for i, stored_vec in enumerate(self._embedding_matrix):
            sim = _cosine_similarity(vector, stored_vec)
            similarities.append({"index": i, "similarity": sim})

        similarities.sort(key=lambda x: -x["similarity"])
        return similarities[:top_k]

    # ── 統計 ──

    def stats(self) -> dict:
        return {
            "acoustic_observations": len(self._acoustic_history),
            "semantic_observations": len(self._semantic_history),
            "embedding_observations": len(self._embedding_history),
            "embedding_matrix_size": len(self._embedding_matrix),
        }


# ════════════════════════════════════════════════════════════════════════════════
# 3. Soul Cortex — 3モデル協調オーケストレーター
# ════════════════════════════════════════════════════════════════════════════════

class SoulCortex:
    """
    3つのモデル（Whisper / Bonsai / MiniLM）を協調させる
    オーケストレーター。

    処理フロー:
      1. Whisperが音声を処理 → 音響観測をShared Stateに書き込み
      2. Bonsaiがテキスト + 音響観測 + 埋込類似度を受け取り → 意味観測を書き込み
      3. MiniLMがテキスト + 重要度を受け取り → 埋込観測を書き込み
      4. 3つの観測をベイズ統合してSOULを更新

    モデル間の情報交換:
      Whisper → Bonsai: acoustic_valence/arousal が emotion の確信度を補強
      Bonsai → MiniLM:  importance が embedding の重み付けに影響
      MiniLM → Bonsai:  semantic_context（類似エピソード）が文脈理解を補強
    """

    def __init__(self, soul: dict, cortex: "Cortex | None" = None):
        self.shared_state = SharedSoulState(soul)
        self.soul = soul
        self.cortex = cortex  # CORTEX.binから読み込んだ設定（Noneならデフォルト）

        # CORTEXから情報交換パラメータを取得（設定がなければデフォルト値）
        if cortex:
            cfg = cortex.cortex
            self._acoustic_boost_weight = cfg.acoustic_boost_weight
            self._acoustic_mismatch_penalty = cfg.acoustic_mismatch_penalty
            self._acoustic_confidence_threshold = cfg.acoustic_confidence_threshold
            self._context_boost_weight = cfg.context_boost_weight
            self._context_similarity_threshold = cfg.context_similarity_threshold
            self._bayesian_sigma_floor = cfg.bayesian_sigma_floor
            self._obs_sigma_base = cfg.obs_sigma_base
            self._obs_sigma_range = cfg.obs_sigma_range
        else:
            # デフォルト値（CORTEX未接続時のフォールバック）
            self._acoustic_boost_weight = 0.2
            self._acoustic_mismatch_penalty = 0.15
            self._acoustic_confidence_threshold = 0.3
            self._context_boost_weight = 0.1
            self._context_similarity_threshold = 0.7
            self._bayesian_sigma_floor = 0.02
            self._obs_sigma_base = 0.05
            self._obs_sigma_range = 0.5

        # ── Watch Point 生態系の初期化 ──
        self.wp_manager = None
        self.wp_rules = None
        if WatchPointManager is not None:
            if cortex and hasattr(cortex, "watchpoint"):
                wpc = cortex.watchpoint
                self.wp_manager = WatchPointManager(
                    max_active=wpc.max_active,
                    probation_trials=wpc.probation_trials,
                    min_hits_to_graduate=wpc.min_hits_to_graduate,
                    fitness_floor=wpc.fitness_floor,
                    decay_half_life_days=wpc.decay_half_life_days,
                    merge_similarity_threshold=wpc.merge_similarity_threshold,
                    observation_window=wpc.observation_window,
                )
                self.wp_rules = WatchPointRules(
                    topic_concentration_threshold=wpc.topic_concentration_threshold,
                    emotion_drift_threshold=wpc.emotion_drift_threshold,
                    importance_spike_count=wpc.importance_spike_count,
                    importance_spike_threshold=wpc.importance_spike_threshold,
                    personality_sigma_spike=wpc.personality_sigma_spike,
                )
            else:
                self.wp_manager = WatchPointManager()
                self.wp_rules = WatchPointRules()
        # WP評価用の履歴バッファ
        self._topic_history: List[Dict[str, float]] = []
        self._episode_history: List[Dict[str, Any]] = []
        # SOULからWPを復元（永続化対応）
        if self.wp_manager is not None:
            existing_wps = soul.get("watchpoints", [])
            if existing_wps:
                self.wp_manager.from_list(existing_wps)

    # ── ファクトリ: CORTEX.binから生成 ──

    @classmethod
    def from_cortex_file(cls, soul: dict, cortex_path: str) -> "SoulCortex":
        """
        CORTEX.binファイルからSoulCortexを初期化する。

        Pi 5実機での起動フロー:
          1. Core基板のフラッシュからCORTEX.binを読み込む
          2. SoulCortex.from_cortex_file(soul, "/dev/flash/CORTEX.bin")
          3. 各モデルがCORTEXの設定に従って動作開始
        """
        if CortexManager is None:
            raise ImportError("cortex_manager module not available")
        mgr = CortexManager()
        cortex = mgr.load(cortex_path)
        return cls(soul, cortex)

    # ── Phase 1: 音響処理 (Whisper) ──

    def process_acoustic(self, audio_features: dict) -> AcousticObservation:
        """
        Whisperが音声を処理し、音響観測を生成する。

        実際のPi 5実装では:
          whisper.cpp → テキスト + 韻律特徴を同時抽出
          VADで区切られた発話区間ごとに観測を生成

        Args:
            audio_features: Whisperから抽出された音響特徴
                {pitch_mean, pitch_variance, tempo, pause_ratio,
                 energy_mean, energy_variance, duration, speech_ratio}
        """
        obs = AcousticObservation(
            pitch_mean=audio_features.get("pitch_mean", 0.5),
            pitch_variance=audio_features.get("pitch_variance", 0.1),
            tempo=audio_features.get("tempo", 0.5),
            pause_ratio=audio_features.get("pause_ratio", 0.2),
            energy_mean=audio_features.get("energy_mean", 0.5),
            energy_variance=audio_features.get("energy_variance", 0.1),
            duration_seconds=audio_features.get("duration", 0.0),
            speech_ratio=audio_features.get("speech_ratio", 0.8),
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        # 音響からの感情推定（Valence-Arousalモデル）
        obs.acoustic_valence = _estimate_valence(obs)
        obs.acoustic_arousal = _estimate_arousal(obs)
        obs.acoustic_confidence = _acoustic_confidence(obs)

        # Shared Stateに書き込み
        self.shared_state.write_acoustic(obs)
        return obs

    # ── Phase 2: 意味抽出 (Bonsai) ──

    def process_semantic(
        self,
        text_features: dict,
        acoustic_obs: Optional[AcousticObservation] = None,
    ) -> SemanticObservation:
        """
        Bonsaiがテキスト特徴を処理し、意味観測を生成する。

        音響層と埋込層からの情報交換を統合:
          - acoustic_obs: Whisperからの感情証拠（確信度ブースト）
          - semantic_context: MiniLMからの類似エピソード（コンテキスト補強）

        Args:
            text_features: Bonsaiが抽出した意味特徴
                {personality_signals, emotion_distribution, importance,
                 topic_distribution, value_signals}
            acoustic_obs: Whisperからの音響観測（Noneならテキストのみ）
        """
        obs = SemanticObservation(
            personality_signals=text_features.get("personality_signals", []),
            emotion_distribution=text_features.get("emotion_distribution", {}),
            importance=text_features.get("importance", 0.5),
            importance_confidence=text_features.get("importance_confidence", 0.5),
            topic_distribution=text_features.get("topic_distribution", {}),
            value_signals=text_features.get("value_signals", {}),
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        # ── 情報交換 1: Whisper → Bonsai ──
        # 音響証拠で感情の確信度を補強
        if acoustic_obs is None:
            acoustic_obs = self.shared_state.read_latest_acoustic()

        if acoustic_obs and acoustic_obs.acoustic_confidence > self._acoustic_confidence_threshold:
            obs.acoustic_boost = self._compute_acoustic_boost(obs, acoustic_obs)
            # 性格シグナルの確信度を音響証拠で補強
            for sig in obs.personality_signals:
                sig["confidence"] = min(
                    1.0,
                    sig["confidence"] + obs.acoustic_boost * 0.2
                )

        # ── 情報交換 3: MiniLM → Bonsai ──
        # 類似エピソードの文脈で重要度を調整
        latest_emb = self.shared_state.read_latest_embedding()
        if latest_emb and latest_emb.nearest_episodes:
            context_boost = self._compute_context_boost(latest_emb.nearest_episodes)
            # 過去に類似したエピソードがあれば、パターンの確信度が上がる
            for sig in obs.personality_signals:
                sig["confidence"] = min(1.0, sig["confidence"] + context_boost * 0.1)

        # Shared Stateに書き込み
        self.shared_state.write_semantic(obs)
        return obs

    # ── Phase 3: 埋め込み生成 (MiniLM) ──

    def process_embedding(
        self,
        text: str,
        embedding_vector: List[float],
    ) -> EmbeddingObservation:
        """
        MiniLMがテキストの埋め込みベクトルを生成し、
        エピソード間の関係性を構築する。

        Bonsaiからの重要度で重み付け:
          重要なエピソードの埋め込みはクラスタ中心により強く影響する

        Args:
            text: 入力テキスト（埋め込み生成用）
            embedding_vector: MiniLMが生成した512次元ベクトル
        """
        obs = EmbeddingObservation(
            vector=embedding_vector,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        # ── 情報交換 2: Bonsai → MiniLM ──
        # 重要度でベクトルの重みを調整
        latest_sem = self.shared_state.read_latest_semantic()
        if latest_sem:
            obs.weighted_importance = latest_sem.importance

        # 類似エピソード検索
        obs.nearest_episodes = self.shared_state.find_similar_episodes(
            embedding_vector, top_k=5
        )

        # トピッククラスタへの割り当て（簡易版: 類似度ベースのソフトクラスタリング）
        if obs.nearest_episodes:
            obs.cluster_assignment = self._soft_cluster(obs.nearest_episodes)

        # Shared Stateに書き込み
        self.shared_state.write_embedding(obs)
        return obs

    # ── Phase 4: ベイズ統合 (SOUL更新) ──

    def integrate_observations(
        self,
        acoustic: Optional[AcousticObservation],
        semantic: SemanticObservation,
        embedding: Optional[EmbeddingObservation],
        raw_text: str = "",
    ) -> dict:
        """
        3つの観測をベイズ統合してSOULを更新する。

        統合原則:
          - 複数のモデルが同じ次元に証拠を出した場合、ベイズ更新で統合
          - 音響証拠 + 意味証拠 → 感情の確信度が高まる
          - 埋め込み類似度 → エピソード間パターンが性格に寄与
        """
        # 1. Core Identity のベイズ更新（意味層 + 音響層の統合確信度）
        for sig in semantic.personality_signals:
            dim = sig.get("dimension", "")
            if dim in self.soul.get("core_identity", {}):
                prior = self.soul["core_identity"][dim]
                confidence = sig["confidence"]

                # 音響証拠による確信度ブースト
                if acoustic and acoustic.acoustic_confidence > 0.3:
                    confidence = min(1.0, confidence + semantic.acoustic_boost * 0.15)

                new_mu, new_sigma = bayesian_update_gaussian(
                    prior["mu"], prior["sigma"],
                    sig["value"], confidence,
                    sigma_floor=self._bayesian_sigma_floor,
                    obs_sigma_base=self._obs_sigma_base,
                    obs_sigma_range=self._obs_sigma_range,
                )
                self.soul["core_identity"][dim]["mu"] = round(new_mu, 4)
                self.soul["core_identity"][dim]["sigma"] = round(new_sigma, 4)

        # 2. エピソード追加（3層の観測を統合した豊かなエピソード）
        episode = {
            "id": f"ep_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "importance": semantic.importance,
            "emotion_distribution": semantic.emotion_distribution,
            "personality_signals": semantic.personality_signals,
            "topic_distribution": semantic.topic_distribution,
            "value_signals": semantic.value_signals,
            "weight": 1.0,
            # 音響層の寄与
            "acoustic": acoustic.to_dict() if acoustic else None,
            # 埋込層の寄与
            "embedding_hash": (
                hashlib.sha256(json.dumps(embedding.vector[:8]).encode()).hexdigest()[:12]
                if embedding and embedding.vector else None
            ),
            "nearest_episodes": (
                embedding.nearest_episodes[:3] if embedding else []
            ),
            "cluster_assignment": (
                embedding.cluster_assignment if embedding else {}
            ),
        }
        self.soul["episodic_memory"]["recent"].append(episode)

        # 3. Semantic Map 更新（意味層のトピック・価値観分布）
        for topic, weight in semantic.topic_distribution.items():
            interests = self.soul["semantic_map"]["interests"]
            interests[topic] = interests.get(topic, 0) + weight

        for value, strength in semantic.value_signals.items():
            values = self.soul["semantic_map"]["values"]
            values[value] = values.get(value, 0) + strength

        # 4. 統計更新
        self.soul["stats"]["total_episodes"] += 1
        self.soul["stats"]["total_updates"] += 1
        self.soul["updated_at"] = datetime.now(timezone.utc).isoformat()

        # 5. Watch Point 生態系の更新（WPMがあれば）
        self._update_watchpoints(semantic, episode)

        return self.soul

    # ── Watch Point 連携 ──

    def _update_watchpoints(
        self,
        semantic: "SemanticObservation",
        episode: dict,
    ) -> None:
        """
        新エピソードをWatch Point観測に連携し、必要に応じて新WPを提案する。

        フロー:
          1. 履歴バッファを更新（topic_distribution / importance）
          2. WatchPointRulesで新WP候補を検出
          3. propose()で候補を生態系に提案
          4. 既存WPにepisode観測を記録（関連targetのWPのみ）
        """
        if self.wp_manager is None or self.wp_rules is None:
            return

        # 1. 履歴更新
        if semantic.topic_distribution:
            self._topic_history.append(dict(semantic.topic_distribution))
            self._topic_history = self._topic_history[-50:]  # 直近50件
        self._episode_history.append(episode)
        self._episode_history = self._episode_history[-50:]

        # 2-3. WP提案
        for candidate in self.wp_rules.check_topic_concentration(self._topic_history):
            self.wp_manager.propose(
                target=candidate["target"],
                trigger=candidate["trigger"],
                priority=candidate["priority"],
                description=candidate["description"],
            )
        for candidate in self.wp_rules.check_importance_spike(self._episode_history):
            self.wp_manager.propose(
                target=candidate["target"],
                trigger=candidate["trigger"],
                priority=candidate["priority"],
                description=candidate["description"],
            )

        # 4. 既存WPに観測を記録
        for wp in self.wp_manager.active():
            # target が topic_distribution または personality_signals にマッチするか判定
            matched_value: Optional[float] = None
            matched_gain: float = 0.0

            # (a) トピック合致
            if wp.target in semantic.topic_distribution:
                matched_value = semantic.topic_distribution[wp.target]
                # 情報利得は重要度 × 観測強度
                matched_gain = semantic.importance * matched_value * 0.5

            # (b) 重要度関連ターゲット ("career_decisions" 等)
            elif any(wp.target.startswith(t) for t in semantic.topic_distribution.keys()):
                # 重要エピソードなら情報利得あり
                if semantic.importance >= 0.6:
                    matched_value = semantic.importance
                    matched_gain = semantic.importance * 0.3

            if matched_value is not None:
                self.wp_manager.observe(
                    wp.id,
                    value=matched_value,
                    information_gain=matched_gain,
                )

    def evolve_watchpoints(self) -> Optional[Dict[str, Any]]:
        """
        Watch Point生態系の1サイクル進化処理を実行する。

        デバイス起動時または定期（例: 1日1回）に呼び出す想定。

        - 非貢献WPをDYINGへ
        - DYING → CULLED（蒸留対象アーカイブ）
        - 類似WPの統合
        - 蒸留: CULLED WPの学習結果をSOULのcore_identityへ還元
        """
        if self.wp_manager is None:
            return None

        stats = self.wp_manager.evolve()
        distilled = self.wp_manager.distill_culled(self.soul)
        stats["distilled"] = distilled

        # WP状態をSOULに書き戻し（永続化）
        self.soul["watchpoints"] = self.wp_manager.to_list()
        return stats

    # ── フルパイプライン（E2E） ──

    def process(
        self,
        text: str,
        audio_features: Optional[dict] = None,
        text_features: Optional[dict] = None,
        embedding_vector: Optional[List[float]] = None,
    ) -> dict:
        """
        フル3モデル協調パイプライン。

        Phase 1: Whisper (音響) → acoustic_obs
        Phase 2: Bonsai (意味) → semantic_obs  ← acoustic_obsを参照
        Phase 3: MiniLM (埋込) → embedding_obs ← semantic_obsを参照
        Phase 4: ベイズ統合 → SOUL更新

        各Phaseの入力は外部モデルから渡されることを想定。
        テスト時はダミーデータで呼び出し可能。
        """
        # Phase 1
        acoustic_obs = None
        if audio_features:
            acoustic_obs = self.process_acoustic(audio_features)

        # Phase 2
        if text_features:
            semantic_obs = self.process_semantic(text_features, acoustic_obs)
        else:
            # テキスト特徴が未抽出ならダミー
            semantic_obs = SemanticObservation(
                importance=0.5,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )

        # Phase 3
        embedding_obs = None
        if embedding_vector:
            embedding_obs = self.process_embedding(text, embedding_vector)

        # Phase 4
        return self.integrate_observations(
            acoustic_obs, semantic_obs, embedding_obs, text
        )

    # ══════════════════════════════════════════════════════════════════════════
    # 内部: 情報交換ロジック
    # ══════════════════════════════════════════════════════════════════════════

    def _compute_acoustic_boost(
        self,
        semantic: SemanticObservation,
        acoustic: AcousticObservation,
    ) -> float:
        """
        音響証拠と意味証拠の一致度からブースト量を計算。

        例: Bonsaiが「joy」を検出 + Whisperが高valence/高arousal
            → 確信度ブースト = 高い

        例: Bonsaiが「sadness」を検出 + Whisperが低valence/低arousal
            → 確信度ブースト = 高い（一致している）

        例: Bonsaiが「joy」を検出 + Whisperが低valence
            → 確信度ブースト = 低い（矛盾 → 確信度を下げるべき）
        """
        if not semantic.emotion_distribution:
            return 0.0

        # 感情の極性を推定
        positive_emotions = {"joy", "excitement", "contentment", "curiosity", "hope"}
        negative_emotions = {"sadness", "anxiety", "anger", "frustration", "fear"}

        semantic_valence = 0.5
        for emotion, prob in semantic.emotion_distribution.items():
            if emotion in positive_emotions:
                semantic_valence += prob * 0.5
            elif emotion in negative_emotions:
                semantic_valence -= prob * 0.5

        semantic_valence = max(0, min(1, semantic_valence))

        # 音響valenceとの一致度
        agreement = 1.0 - abs(semantic_valence - acoustic.acoustic_valence)

        # 一致 → ブースト、不一致 → ペナルティ（CORTEXパラメータで制御）
        if agreement > 0.6:
            return agreement * self._acoustic_boost_weight * acoustic.acoustic_confidence
        else:
            return -(1.0 - agreement) * self._acoustic_mismatch_penalty * acoustic.acoustic_confidence

    def _compute_context_boost(self, nearest_episodes: List[Dict]) -> float:
        """
        類似エピソードの存在がパターン確信度を高める。

        多くの類似エピソードがある = このパターンは反復している = 確信度UP
        """
        if not nearest_episodes:
            return 0.0

        # 高い類似度のエピソードが多いほどブースト（CORTEXパラメータで制御）
        high_sim = [ep for ep in nearest_episodes
                    if ep.get("similarity", 0) > self._context_similarity_threshold]
        return min(0.3, len(high_sim) * self._context_boost_weight)

    def _soft_cluster(self, nearest_episodes: List[Dict]) -> Dict[str, float]:
        """類似度ベースのソフトクラスタリング"""
        clusters: Dict[str, float] = {}
        total = 0.0
        for i, ep in enumerate(nearest_episodes[:5]):
            cid = f"cluster_{ep.get('index', i) // 5}"
            sim = ep.get("similarity", 0.5)
            clusters[cid] = clusters.get(cid, 0) + sim
            total += sim

        if total > 0:
            return {k: round(v / total, 3) for k, v in clusters.items()}
        return {}


# ════════════════════════════════════════════════════════════════════════════════
# 4. ユーティリティ関数
# ════════════════════════════════════════════════════════════════════════════════

def bayesian_update_gaussian(
    prior_mu: float,
    prior_sigma: float,
    observation: float,
    obs_confidence: float,
    sigma_floor: float = 0.02,
    obs_sigma_base: float = 0.05,
    obs_sigma_range: float = 0.5,
) -> Tuple[float, float]:
    """ガウス分布の共役事前分布によるベイズ更新（CORTEXパラメータ対応版）"""
    obs_sigma = (1.0 - obs_confidence) * obs_sigma_range + obs_sigma_base
    prior_var = prior_sigma ** 2
    obs_var = obs_sigma ** 2
    posterior_var = 1.0 / (1.0 / prior_var + 1.0 / obs_var)
    posterior_mu = posterior_var * (prior_mu / prior_var + observation / obs_var)
    posterior_sigma = math.sqrt(posterior_var)
    return posterior_mu, max(posterior_sigma, sigma_floor)


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    """コサイン類似度"""
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _estimate_valence(obs: AcousticObservation) -> float:
    """音響特徴からvalenceを推定（簡易モデル）"""
    # 高ピッチ + 高テンポ + 高エネルギー → ポジティブ傾向
    v = 0.5
    v += (obs.pitch_mean - 0.5) * 0.3
    v += (obs.tempo - 0.5) * 0.2
    v += (obs.energy_mean - 0.5) * 0.2
    # ポーズが多い → ネガティブ傾向
    v -= (obs.pause_ratio - 0.2) * 0.3
    return max(0.0, min(1.0, v))


def _estimate_arousal(obs: AcousticObservation) -> float:
    """音響特徴からarousalを推定（簡易モデル）"""
    # 高テンポ + 高エネルギー変動 + 高ピッチ変動 → 高覚醒
    a = 0.3
    a += obs.tempo * 0.3
    a += obs.energy_variance * 0.3
    a += obs.pitch_variance * 0.2
    return max(0.0, min(1.0, a))


def _acoustic_confidence(obs: AcousticObservation) -> float:
    """音響推定の確信度（発話時間と発話比率に基づく）"""
    # 長い発話 + 高い発話比率 → 高い確信度
    duration_factor = min(1.0, obs.duration_seconds / 30.0)  # 30秒で飽和
    speech_factor = obs.speech_ratio
    return 0.2 + 0.6 * duration_factor * speech_factor


# ════════════════════════════════════════════════════════════════════════════════
# 5. デモ
# ════════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    from soul_schema import create_empty_soul

    print("═══ Ghost-Printer Soul Cortex: 3-Model Collaborative Demo ═══\n")

    soul = create_empty_soul("demo_user")
    cortex = SoulCortex(soul)

    # シナリオ: ユーザーが興奮気味に新しいプロジェクトについて話している

    # Phase 1: 音響特徴（高テンポ, 高ピッチ変動 → 興奮）
    audio = {
        "pitch_mean": 0.65,
        "pitch_variance": 0.35,
        "tempo": 0.72,
        "pause_ratio": 0.10,
        "energy_mean": 0.68,
        "energy_variance": 0.25,
        "duration": 45.0,
        "speech_ratio": 0.85,
    }
    a_obs = cortex.process_acoustic(audio)
    print(f"Phase 1 (Whisper): valence={a_obs.acoustic_valence:.2f}, arousal={a_obs.acoustic_arousal:.2f}, conf={a_obs.acoustic_confidence:.2f}")

    # Phase 2: 意味特徴（Bonsaiの抽出結果 + 音響補強）
    text_feat = {
        "personality_signals": [
            {"dimension": "openness", "value": 0.82, "confidence": 0.6},
            {"dimension": "curiosity", "value": 0.88, "confidence": 0.7},
            {"dimension": "extraversion", "value": 0.55, "confidence": 0.4},
        ],
        "emotion_distribution": {"excitement": 0.7, "curiosity": 0.2, "joy": 0.1},
        "importance": 0.8,
        "importance_confidence": 0.6,
        "topic_distribution": {"technology": 0.5, "project": 0.3, "innovation": 0.2},
        "value_signals": {"creativity": 0.7, "autonomy": 0.3},
    }
    s_obs = cortex.process_semantic(text_feat, a_obs)
    print(f"Phase 2 (Bonsai):  importance={s_obs.importance:.2f}, acoustic_boost={s_obs.acoustic_boost:.3f}")
    print(f"  Personality confidence after boost:")
    for sig in s_obs.personality_signals:
        print(f"    {sig['dimension']:20s} conf={sig['confidence']:.3f}")

    # Phase 3: 埋め込みベクトル（512次元のダミー）
    import random
    random.seed(42)
    fake_embedding = [random.gauss(0, 1) for _ in range(512)]
    e_obs = cortex.process_embedding("新しいプロジェクトのアイデア", fake_embedding)
    print(f"Phase 3 (MiniLM):  vector_dim={len(e_obs.vector)}, weighted_importance={e_obs.weighted_importance:.2f}")

    # Phase 4: ベイズ統合
    updated_soul = cortex.integrate_observations(a_obs, s_obs, e_obs)
    print(f"\nPhase 4 (Integration):")
    print(f"  Episodes: {len(updated_soul['episodic_memory']['recent'])}")
    for dim in ["openness", "curiosity", "extraversion"]:
        d = updated_soul["core_identity"][dim]
        print(f"  {dim:20s} μ={d['mu']:.4f}, σ={d['sigma']:.4f}")

    # 2回目の入力（静かに読書）
    print("\n── 2nd input: 静かに読書 ──")
    audio2 = {
        "pitch_mean": 0.35, "pitch_variance": 0.08,
        "tempo": 0.25, "pause_ratio": 0.60,
        "energy_mean": 0.25, "energy_variance": 0.05,
        "duration": 10.0, "speech_ratio": 0.3,
    }
    text_feat2 = {
        "personality_signals": [
            {"dimension": "extraversion", "value": 0.22, "confidence": 0.5},
            {"dimension": "openness", "value": 0.70, "confidence": 0.4},
        ],
        "emotion_distribution": {"contentment": 0.6, "calm": 0.3, "curiosity": 0.1},
        "importance": 0.3,
        "importance_confidence": 0.5,
        "topic_distribution": {"reading": 0.6, "relaxation": 0.4},
        "value_signals": {"introspection": 0.8},
    }
    fake_embedding2 = [random.gauss(0, 1) for _ in range(512)]

    updated = cortex.process(
        text="一人で静かに読書を楽しんだ",
        audio_features=audio2,
        text_features=text_feat2,
        embedding_vector=fake_embedding2,
    )

    print(f"  Episodes: {len(updated['episodic_memory']['recent'])}")
    for dim in ["openness", "curiosity", "extraversion"]:
        d = updated["core_identity"][dim]
        print(f"  {dim:20s} μ={d['mu']:.4f}, σ={d['sigma']:.4f}")

    print(f"\n  Shared State: {cortex.shared_state.stats()}")
    print("\n═══ Demo Complete ═══")
