#!/bin/bash
# Ghost-Printer A1 — セットアップスクリプト
# macOS用。ターミナルで実行してください。

set -e

echo "╔══════════════════════════════════════════╗"
echo "║    Ghost-Printer A1 — Setup              ║"
echo "╚══════════════════════════════════════════╝"
echo ""

# ── 1. Python依存パッケージ ──
echo "📦 Python依存パッケージをインストール中..."
pip3 install -r requirements.txt
echo "   ✅ 完了"
echo ""

# ── 2. Ollama確認 ──
if command -v ollama &> /dev/null; then
    echo "✅ Ollamaは既にインストール済みです"
else
    echo "📥 Ollamaをインストールします..."
    echo "   ブラウザで https://ollama.com を開いてダウンロードしてください。"
    echo "   または: brew install ollama"
    echo ""
    read -p "   インストール完了後、Enterを押してください..."
fi

# ── 3. Ollamaサービス確認 ──
echo ""
echo "🔍 Ollamaサービスを確認中..."
if curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
    echo "   ✅ Ollamaは起動中です"
else
    echo "   ⚠️  Ollamaが起動していません。別のターミナルで以下を実行してください:"
    echo "   ollama serve"
    echo ""
    read -p "   起動後、Enterを押してください..."
fi

# ── 4. モデル取得 ──
MODEL="gemma3:4b"
echo ""
echo "🤖 モデル '$MODEL' を確認中..."
if ollama list 2>/dev/null | grep -q "gemma3:4b"; then
    echo "   ✅ モデルは既にダウンロード済みです"
else
    echo "   📥 モデルをダウンロード中（約3GB、初回のみ）..."
    ollama pull $MODEL
    echo "   ✅ 完了"
fi

# ── 5. SOUL初期化 ──
echo ""
echo "🔧 SOULデータを初期化中..."
python3 main.py --init
echo ""

# ── 6. 接続テスト ──
echo "🔍 接続テスト..."
python3 main.py --check
echo ""

echo "╔══════════════════════════════════════════╗"
echo "║    セットアップ完了！                    ║"
echo "║    python3 main.py で開始できます        ║"
echo "╚══════════════════════════════════════════╝"
