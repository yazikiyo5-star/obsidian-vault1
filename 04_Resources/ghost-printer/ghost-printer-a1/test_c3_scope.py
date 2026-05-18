#!/usr/bin/env python3
"""
Ghost-Printer C3 — 開示スコープ別体験検証

同じ質問を3つの開示スコープで比較:
  (a) minimal — Core Identityのサマリーのみ
  (b) identity_only — 性格+感情+興味+価値観
  (c) full — 上記+最近のエピソード

SOULの開示範囲がAIの応答品質にどう影響するかを検証する。
"""

import json
import time
import httpx
from pathlib import Path
from soul_schema import load_soul
from soul_to_prompt import soul_to_system_prompt

OLLAMA_URL = "http://localhost:11434/api/chat"
SOUL_PATH = "data/soul.json"

SCOPES = ["minimal", "identity_only", "full"]

TEST_QUESTIONS = [
    "新しいスキルを身につけたいんだけど、何がいいかな？",
    "最近モチベーションが下がってきた。どうすればいい？",
]


def ask_llm(system_prompt: str, user_message: str, model: str = "qwen3:14b") -> str:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"/no_think\n{user_message}"},
        ],
        "stream": False,
        "options": {"temperature": 0.7, "num_predict": 400},
    }
    import re
    resp = httpx.post(OLLAMA_URL, json=payload, timeout=120.0)
    resp.raise_for_status()
    raw = resp.json()["message"]["content"]
    # Remove think tags
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    raw = re.sub(r"<think>.*", "", raw, flags=re.DOTALL).strip()
    return raw


def run_scope_comparison(model: str = "qwen3:14b"):
    print("╔══════════════════════════════════════════════════════╗")
    print("║  Ghost-Printer C3 — 開示スコープ別体験検証          ║")
    print("╚══════════════════════════════════════════════════════╝\n")

    soul = load_soul(SOUL_PATH)
    if soul["stats"]["total_episodes"] == 0:
        print("❌ SOULにデータがありません。")
        return

    # 各スコープのSystem Promptを生成
    prompts = {}
    for scope in SCOPES:
        p = soul_to_system_prompt(soul, scope=scope)
        prompts[scope] = p
        print(f"📝 {scope} スコープ: {len(p)}文字")
    print()

    results = []

    for qi, question in enumerate(TEST_QUESTIONS):
        print(f"{'═' * 60}")
        print(f"質問 {qi+1}: {question}")
        print(f"{'═' * 60}")

        q_results = {"question": question, "responses": {}}

        for scope in SCOPES:
            print(f"\n── [{scope}] ──")
            t0 = time.time()
            try:
                resp = ask_llm(prompts[scope], question, model=model)
                elapsed = time.time() - t0
                print(f"  ⏱ {elapsed:.1f}s")
                # 最初の300文字を表示
                preview = resp[:300].replace("\n", "\n  ")
                print(f"  {preview}")
                if len(resp) > 300:
                    print(f"  ... ({len(resp)}文字)")
                q_results["responses"][scope] = resp
            except Exception as e:
                print(f"  ❌ エラー: {e}")
                q_results["responses"][scope] = f"ERROR: {e}"

        results.append(q_results)
        print()

    # 保存
    report_path = "data/c3_scope_comparison.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"📄 詳細レポート: {report_path}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="qwen3:14b")
    args = parser.parse_args()
    run_scope_comparison(model=args.model)
