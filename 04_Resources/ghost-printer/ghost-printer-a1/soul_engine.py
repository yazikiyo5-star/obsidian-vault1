"""
Ghost-Printer A1 — SOULベイズ更新エンジン

エピソードから抽出された性格シグナルで core_identity をベイズ更新し、
エピソードの重み減衰（exponential decay）と、
personality prior への統合（distillation）を行う。
"""

import math
from datetime import datetime, timezone, timedelta
from soul_schema import create_episode, load_soul, save_soul


# ── ベイズ更新 ──

def bayesian_update_gaussian(
    prior_mu: float,
    prior_sigma: float,
    observation: float,
    obs_confidence: float,
) -> tuple[float, float]:
    """
    ガウス分布の共役事前分布によるベイズ更新。

    prior:       N(prior_mu, prior_sigma²)
    likelihood:  観測値 observation、精度 = obs_confidence
                 obs_sigma = (1 - obs_confidence) * 0.5 + 0.05
                 （confidence=1.0 → σ=0.05, confidence=0.0 → σ=0.55）

    Returns:
        (posterior_mu, posterior_sigma)
    """
    # 観測のノイズ（confidence が高いほど σ が小さい）
    obs_sigma = (1.0 - obs_confidence) * 0.5 + 0.05

    prior_var = prior_sigma ** 2
    obs_var = obs_sigma ** 2

    # ベイズ更新の閉形式解
    posterior_var = 1.0 / (1.0 / prior_var + 1.0 / obs_var)
    posterior_mu = posterior_var * (prior_mu / prior_var + observation / obs_var)
    posterior_sigma = math.sqrt(posterior_var)

    # σ の下限（完全な確信にはさせない）
    posterior_sigma = max(posterior_sigma, 0.02)

    return posterior_mu, posterior_sigma


# ── エピソード重み減衰 ──

DECAY_HALF_LIFE_DAYS = 30  # 30日で重みが半減

def decay_episode_weights(soul: dict) -> None:
    """
    全エピソードの weight を exponential decay で更新する。
    重みが 0.05 未満になったエピソードは削除候補。
    """
    now = datetime.now(timezone.utc)
    decay_rate = math.log(2) / DECAY_HALF_LIFE_DAYS

    surviving = []
    for ep in soul["episodic_memory"]["recent"]:
        ts = datetime.fromisoformat(ep["timestamp"])
        days_elapsed = (now - ts).total_seconds() / 86400

        # 重要度が高いほど減衰が遅い（importance が decay を緩和）
        effective_rate = decay_rate * (1.0 - ep["importance"] * 0.5)
        new_weight = ep["weight"] * math.exp(-effective_rate * max(days_elapsed, 0))

        if new_weight >= 0.05:
            ep["weight"] = round(new_weight, 4)
            surviving.append(ep)
        # else: distill into core_identity before discarding
        else:
            _distill_episode(soul, ep)

    soul["episodic_memory"]["recent"] = surviving


def _distill_episode(soul: dict, episode: dict) -> None:
    """
    消えゆくエピソードの性格シグナルを core_identity に最終統合する。
    これが「記憶が性格に溶ける」プロセス。
    """
    for sig in episode.get("personality_signals", []):
        dim = sig["dimension"]
        if dim in soul["core_identity"]:
            prior = soul["core_identity"][dim]
            # 低重みエピソードなので confidence を下げて穏やかに統合
            adj_confidence = sig["confidence"] * episode["weight"] * 0.5
            if adj_confidence > 0.01:
                new_mu, new_sigma = bayesian_update_gaussian(
                    prior["mu"], prior["sigma"],
                    sig["value"], adj_confidence,
                )
                soul["core_identity"][dim]["mu"] = round(new_mu, 4)
                soul["core_identity"][dim]["sigma"] = round(new_sigma, 4)


# ── メイン更新関数 ──

def update_soul(soul: dict, delta: dict, raw_text: str, context: dict | None = None) -> dict:
    """
    抽出された delta で SOUL を更新する。

    1. core_identity をベイズ更新
    2. エピソードを追加
    3. semantic_map を更新
    4. 既存エピソードの重み減衰を実行
    5. 統計を更新

    Args:
        soul: 現在のSOUL辞書
        delta: extractor が返した抽出結果
        raw_text: 元のテキスト
        context: オプショナルなコンテキスト

    Returns:
        更新されたSOUL辞書
    """
    # 1. Core Identity のベイズ更新
    for sig in delta.get("personality_signals", []):
        dim = sig["dimension"]
        if dim in soul["core_identity"]:
            prior = soul["core_identity"][dim]
            new_mu, new_sigma = bayesian_update_gaussian(
                prior["mu"], prior["sigma"],
                sig["value"], sig["confidence"],
            )
            soul["core_identity"][dim]["mu"] = round(new_mu, 4)
            soul["core_identity"][dim]["sigma"] = round(new_sigma, 4)

    # 2. エピソード追加
    episode = create_episode(
        text=raw_text,
        importance=delta["importance"],
        emotion=delta["emotion"],
        personality_signals=delta["personality_signals"],
        topics=delta.get("topics", []),
        values=delta.get("values", []),
        summary=delta.get("summary", ""),
        context=context,
    )
    soul["episodic_memory"]["recent"].append(episode)

    # 3. Semantic Map 更新
    _update_semantic_map(soul, delta)

    # 4. 重み減衰（毎回実行）
    decay_episode_weights(soul)

    # 5. 統計更新
    soul["stats"]["total_episodes"] += 1
    soul["stats"]["total_updates"] += 1

    return soul


def _update_semantic_map(soul: dict, delta: dict) -> None:
    """興味トピックと価値観を更新する"""
    interests = soul["semantic_map"]["interests"]
    for topic in delta.get("topics", []):
        topic_lower = topic.lower()
        interests[topic_lower] = interests.get(topic_lower, 0) + 1

    values = soul["semantic_map"]["values"]
    for value in delta.get("values", []):
        value_lower = value.lower()
        values[value_lower] = values.get(value_lower, 0) + 1


# ── SOUL サマリー生成（デバッグ・確認用） ──

def soul_summary(soul: dict) -> str:
    """SOULの現在状態を人間が読める形で表示する"""
    lines = []
    lines.append("═══ SOUL Summary ═══")
    lines.append(f"Updated: {soul.get('updated_at', 'N/A')}")
    lines.append(f"Episodes: {len(soul['episodic_memory']['recent'])} recent")
    lines.append(f"Total inputs: {soul['stats']['total_episodes']}")
    lines.append("")

    # Core Identity
    lines.append("── Core Identity ──")
    for dim, dist in sorted(soul["core_identity"].items()):
        bar_len = int(dist["mu"] * 20)
        bar = "█" * bar_len + "░" * (20 - bar_len)
        confidence = "●" if dist["sigma"] < 0.10 else "◐" if dist["sigma"] < 0.20 else "○"
        lines.append(f"  {confidence} {dim:<22s} {bar} {dist['mu']:.2f} (σ={dist['sigma']:.2f})")

    # Recent emotions
    lines.append("")
    lines.append("── Recent Emotions ──")
    for ep in soul["episodic_memory"]["recent"][-5:]:
        ts = ep.get("timestamp", "")[:16]
        summary = ep.get("summary", ep.get("raw_text", ""))[:40]
        if "emotion" in ep and isinstance(ep["emotion"], dict) and "name" in ep["emotion"]:
            # 旧フォーマット(extractor.py経由): {"name": "joy", "intensity": 0.7}
            em = ep["emotion"]
            lines.append(f"  {ts} | {em['name']:12s} ({em['intensity']:.1f}) | {summary}")
        elif "emotion_distribution" in ep and ep["emotion_distribution"]:
            # Soul Cortex フォーマット: {"joy": 0.3, "sadness": 0.1, ...}
            ed = ep["emotion_distribution"]
            top = max(ed.items(), key=lambda x: x[1])
            lines.append(f"  {ts} | {top[0]:12s} ({top[1]:.1f}) | {summary}")
        else:
            # 音響のみ等、感情情報が取れなかったエピソード
            acoustic = ep.get("acoustic", {})
            if acoustic:
                valence = acoustic.get("emotion", {}).get("valence", 0.5)
                lines.append(f"  {ts} | acoustic    (v={valence:.2f}) | {summary or '(audio only)'}")
            else:
                lines.append(f"  {ts} | (no emotion) | {summary}")

    # Top interests
    interests = soul["semantic_map"]["interests"]
    if interests:
        lines.append("")
        lines.append("── Top Interests ──")
        top = sorted(interests.items(), key=lambda x: -x[1])[:8]
        for topic, count in top:
            lines.append(f"  {topic}: {count}")

    # Top values
    values = soul["semantic_map"]["values"]
    if values:
        lines.append("")
        lines.append("── Core Values ──")
        top_v = sorted(values.items(), key=lambda x: -x[1])[:5]
        for val, count in top_v:
            lines.append(f"  {val}: {count}")

    return "\n".join(lines)
