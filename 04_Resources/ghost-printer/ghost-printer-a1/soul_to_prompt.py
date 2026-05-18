#!/usr/bin/env python3
"""
Ghost-Printer A5 — SOUL → System Prompt 変換器

蓄積されたSOULデータからAI向けのSystem Promptを自動生成する。
これがGhost-Printerの核心: SOULがあることでAIとの対話が変わるかの検証。
"""

import json
from pathlib import Path
from soul_schema import load_soul


def soul_to_system_prompt(soul: dict, scope: str = "full") -> str:
    """
    SOULデータからSystem Promptを生成する。

    Args:
        soul: SOULデータ
        scope: 開示スコープ ("full", "identity_only", "minimal")

    Returns:
        System Prompt文字列
    """
    sections = []

    # ── ヘッダー ──
    sections.append(
        "あなたはこの人のパーソナルアシスタントです。\n"
        "以下はこの人のSOULデータ（パーソナリティプロファイル）です。\n"
        "このデータを参考に、この人に最も適した応答をしてください。\n"
        "ただし「あなたのSOULデータによると…」のような直接的な言及は避け、\n"
        "自然にこの人を理解している人のように振る舞ってください。"
    )

    # ── Core Identity ──
    identity = soul.get("core_identity", {})
    if identity:
        sections.append(_format_identity(identity))

    if scope == "minimal":
        return "\n\n".join(sections)

    # ── 感情の傾向 ──
    recent = soul.get("episodic_memory", {}).get("recent", [])
    if recent:
        sections.append(_format_emotional_tendencies(recent))

    # ── 興味・関心 ──
    interests = soul.get("semantic_map", {}).get("interests", {})
    if interests:
        sections.append(_format_interests(interests))

    # ── 価値観 ──
    values = soul.get("semantic_map", {}).get("values", {})
    if values:
        sections.append(_format_values(values))

    if scope == "identity_only":
        return "\n\n".join(sections)

    # ── 最近のコンテキスト（full scope のみ） ──
    if recent:
        sections.append(_format_recent_context(recent[-5:]))

    return "\n\n".join(sections)


def _format_identity(identity: dict) -> str:
    """Core Identityを自然言語で記述する"""
    lines = ["## この人の性格特性"]

    # 確信度が高い（σ < 0.20）次元のみを記述
    confident = {
        dim: dist for dim, dist in identity.items()
        if dist["sigma"] < 0.20
    }

    if not confident:
        # 確信度が高いものがなければ、すべて記述（初期段階）
        confident = identity

    descriptions = {
        "openness": {
            "high": "新しいアイデアや体験に強い関心を持つ。知的好奇心が旺盛。",
            "low": "慣れ親しんだ方法を好み、安定した環境を重視する。",
        },
        "conscientiousness": {
            "high": "計画的で目標志向。締め切りや約束を大切にする。",
            "low": "柔軟で即興的。厳密なスケジュールより自由を好む。",
        },
        "extraversion": {
            "high": "社交的で活動的。人との交流からエネルギーを得る。",
            "low": "内省的で一人の時間を大切にする。深い思考を好む。",
        },
        "agreeableness": {
            "high": "協調的で思いやりがある。他者の気持ちに敏感。",
            "low": "自分の意見を率直に言う。独立した判断を重視する。",
        },
        "neuroticism": {
            "high": "感情の起伏がある。ストレスに敏感で、物事を深く考える傾向。",
            "low": "情緒が安定しており、ストレス下でも冷静でいられる。",
        },
        "curiosity": {
            "high": "知的探究心が非常に強い。未知の分野にも積極的に飛び込む。",
            "low": "必要な情報を効率的に得ることを好む。",
        },
        "creativity": {
            "high": "独自の発想やアプローチを生み出す力がある。",
            "low": "実証済みの方法を信頼する。",
        },
        "empathy": {
            "high": "他者の感情を深く理解し、共感する力が強い。",
            "low": "論理的・客観的なアプローチを好む。",
        },
        "risk_tolerance": {
            "high": "リスクを恐れず、不確実な状況でも前に進める。",
            "low": "慎重にリスクを評価し、安全な選択を好む。",
        },
        "independence": {
            "high": "自分の判断を信頼し、他者の承認を必要としない。",
            "low": "チームワークやコンセンサスを重視する。",
        },
    }

    for dim, dist in sorted(confident.items(), key=lambda x: x[1]["sigma"]):
        desc_map = descriptions.get(dim, {})
        level = "high" if dist["mu"] > 0.55 else "low" if dist["mu"] < 0.45 else None
        if level and desc_map:
            confidence_note = ""
            if dist["sigma"] < 0.10:
                confidence_note = "（非常に安定した特性）"
            elif dist["sigma"] < 0.15:
                confidence_note = "（安定した特性）"
            lines.append(f"- {desc_map[level]}{confidence_note}")

    return "\n".join(lines)


def _format_emotional_tendencies(recent: list) -> str:
    """感情の傾向を記述する"""
    emotions = {}
    for ep in recent[-20:]:
        em = ep.get("emotion", {})
        name = em.get("name", "neutral")
        intensity = em.get("intensity", 0.3)
        if name not in emotions:
            emotions[name] = {"count": 0, "total_intensity": 0}
        emotions[name]["count"] += 1
        emotions[name]["total_intensity"] += intensity

    if not emotions:
        return ""

    lines = ["## 感情の傾向"]
    top = sorted(emotions.items(), key=lambda x: -x[1]["count"])[:5]
    for name, data in top:
        avg_int = data["total_intensity"] / data["count"]
        lines.append(f"- {name}: {data['count']}回出現, 平均強度 {avg_int:.1f}")

    return "\n".join(lines)


def _format_interests(interests: dict) -> str:
    """興味・関心を記述する"""
    if not interests:
        return ""
    lines = ["## 興味・関心"]
    top = sorted(interests.items(), key=lambda x: -x[1])[:10]
    topics = [t[0] for t in top]
    lines.append(f"主な関心領域: {', '.join(topics)}")
    return "\n".join(lines)


def _format_values(values: dict) -> str:
    """価値観を記述する"""
    if not values:
        return ""
    lines = ["## 大切にしている価値観"]
    top = sorted(values.items(), key=lambda x: -x[1])[:5]
    vals = [v[0] for v in top]
    lines.append(f"{', '.join(vals)}")
    return "\n".join(lines)


def _format_recent_context(recent: list) -> str:
    """最近のコンテキストを記述する"""
    lines = ["## 最近の出来事"]
    for ep in recent:
        summary = ep.get("summary", "")
        if summary:
            lines.append(f"- {summary}")
    return "\n".join(lines)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="SOUL → System Prompt 変換")
    parser.add_argument("--soul-path", default="data/soul.json")
    parser.add_argument("--scope", default="full", choices=["full", "identity_only", "minimal"])
    parser.add_argument("--output", default=None, help="出力ファイルパス（省略で標準出力）")
    args = parser.parse_args()

    soul = load_soul(args.soul_path)

    if soul["stats"]["total_episodes"] == 0:
        print("⚠️  SOULにデータがありません。先にmain.pyでテキストを入力してください。")
        return

    prompt = soul_to_system_prompt(soul, scope=args.scope)

    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(prompt)
        print(f"✅ System Promptを保存: {args.output}")
        print(f"   文字数: {len(prompt)}")
    else:
        print("═══ 生成されたSystem Prompt ═══")
        print(prompt)
        print(f"\n═══ 文字数: {len(prompt)} ═══")


if __name__ == "__main__":
    main()
