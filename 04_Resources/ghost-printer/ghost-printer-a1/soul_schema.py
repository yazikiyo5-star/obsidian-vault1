"""
Ghost-Printer A1 — SOULフォーマット定義

設計思想:
- 開発段階ではJSONで表現（将来はバイナリ .soul に移行）
- 4層構造: Core Identity / Episodic Memory / Semantic Map / Temporal Patterns
- ベイズ的忘却: エピソードが性格に溶け込む設計
"""

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_empty_soul(owner_name: str = "anonymous") -> dict:
    """空のSOULを生成する"""
    return {
        "version": "0.1.0",
        "owner_hash": owner_name,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),

        # ── Layer 1: Core Identity ──
        # 各次元をガウス分布 (μ, σ) で表現
        # σ が大きい = まだ不確か、小さい = 確信が高い
        "core_identity": {
            "openness":          {"mu": 0.5, "sigma": 0.30},
            "conscientiousness": {"mu": 0.5, "sigma": 0.30},
            "extraversion":      {"mu": 0.5, "sigma": 0.30},
            "agreeableness":     {"mu": 0.5, "sigma": 0.30},
            "neuroticism":       {"mu": 0.5, "sigma": 0.30},
            # 追加次元（Ghost-Printer固有）
            "curiosity":         {"mu": 0.5, "sigma": 0.30},
            "creativity":        {"mu": 0.5, "sigma": 0.30},
            "empathy":           {"mu": 0.5, "sigma": 0.30},
            "risk_tolerance":    {"mu": 0.5, "sigma": 0.30},
            "independence":      {"mu": 0.5, "sigma": 0.30},
        },

        # ── Layer 2: Episodic Memory ──
        # recent[]: 直近30日のエピソード
        # compressed[]: 30-180日（将来GMMクラスタリングで実装）
        # distilled: 180日以上 → core_identityに溶け込んで消える
        "episodic_memory": {
            "recent": [],       # list of Episode objects
            "compressed": [],   # 将来実装
        },

        # ── Layer 3: Semantic Map ──
        # 興味・関心のカテゴリ分布
        "semantic_map": {
            "interests": {},    # {"topic": weight} — Dirichlet的に正規化
            "values": {},       # {"value_name": strength}
        },

        # ── Layer 4: Temporal Patterns ──
        # 行動の時間パターン（将来HMMで実装）
        "temporal_patterns": {
            "active_hours": [], # 活動時間帯の記録
            "routines": [],     # 検出されたルーティン
        },

        # メタ情報
        "stats": {
            "total_episodes": 0,
            "total_updates": 0,
        }
    }


def create_episode(
    text: str,
    importance: float,
    emotion: dict,
    personality_signals: list,
    topics: list,
    values: list,
    summary: str,
    context: Optional[dict] = None,
) -> dict:
    """エピソードオブジェクトを生成する"""
    return {
        "id": f"ep_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        "timestamp": _now_iso(),
        "raw_text": text,
        "summary": summary,
        "importance": max(0.0, min(1.0, importance)),
        "emotion": emotion,            # {"name": str, "intensity": 0-1}
        "personality_signals": personality_signals,  # [{"dimension": str, "value": 0-1, "confidence": 0-1}]
        "topics": topics,               # ["topic1", "topic2"]
        "values": values,               # ["value1", "value2"]
        "context": context or {},        # {"location": ..., "time_of_day": ..., etc.}
        "weight": 1.0,                  # exponential decayで減衰する
    }


def load_soul(path: str) -> dict:
    """SOULファイルを読み込む。存在しなければ空のSOULを返す"""
    p = Path(path)
    if p.exists():
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    return create_empty_soul()


def save_soul(soul: dict, path: str) -> None:
    """SOULファイルを保存する"""
    soul["updated_at"] = _now_iso()
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(soul, f, ensure_ascii=False, indent=2)
