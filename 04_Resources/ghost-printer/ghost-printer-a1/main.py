#!/usr/bin/env python3
"""
Ghost-Printer A1 — メインCLI

日記やメモを入力すると、ローカルLLMで性格・感情・重要度を抽出し、
soul.json をベイズ更新する。

使い方:
  python main.py                    # 対話モード
  python main.py --input "テキスト"  # 単発入力
  python main.py --status            # SOUL状態表示
  python main.py --check             # Ollama接続確認

実機向け（Pi 5）:
  python main.py --mic --duration 60           # マイク60秒録音→SOUL更新
  python main.py --flash-cortex data/CORTEX.bin  # SPI FlashへCORTEX書き込み
  python main.py --load-cortex /dev/cortex     # 起動時にCORTEX読込 (またはファイル)
  python main.py --evolve                      # Watch Point 進化サイクル
"""

import argparse
import json
import sys
from pathlib import Path
from datetime import datetime

from soul_schema import create_empty_soul, load_soul, save_soul
from extractor import extract_soul_delta, check_ollama_connection
from soul_engine import update_soul, soul_summary

# デフォルトパス
DEFAULT_SOUL_PATH = str(Path(__file__).parent / "data" / "soul.json")
DEFAULT_CORTEX_PATH = str(Path(__file__).parent / "data" / "CORTEX.bin")
DEFAULT_MODEL = "gemma3:4b"


def cmd_check(args):
    """Ollama接続確認"""
    print("🔍 Ollamaの接続を確認中...")
    status = check_ollama_connection()
    if status["connected"]:
        print(f"✅ Ollama接続OK")
        print(f"   利用可能なモデル:")
        for m in status["models"]:
            marker = " ← 使用中" if m.startswith(args.model) else ""
            print(f"     - {m}{marker}")
        if not any(m.startswith(args.model) for m in status["models"]):
            print(f"\n⚠️  モデル '{args.model}' が見つかりません。")
            print(f"   以下のコマンドでインストールしてください:")
            print(f"   ollama pull {args.model}")
    else:
        print("❌ Ollamaに接続できません。")
        print("   1. Ollamaをインストール: https://ollama.com")
        print("   2. 起動: ollama serve")
        print(f"   3. モデル取得: ollama pull {args.model}")
    return status["connected"]


def cmd_status(args):
    """SOUL状態表示"""
    soul = load_soul(args.soul_path)
    if soul["stats"]["total_episodes"] == 0:
        print("📭 SOULはまだ空です。日記やメモを入力して育てましょう。")
        print(f"   使い方: python main.py")
    else:
        print(soul_summary(soul))
        # Watch Point セクション
        wps = soul.get("watchpoints", [])
        if wps:
            print("\n─── Watch Points ───")
            active = [w for w in wps if w.get("state") in ("nascent", "active", "dormant")]
            print(f"  active: {len(active)} / total: {len(wps)}")
            for w in active[:10]:
                print(f"    [{w['state']:8s}] {w['target']:25s} "
                      f"priority={w.get('priority', 0):.2f} "
                      f"hits={w.get('hit_count', 0)}/{w.get('observation_count', 0)}")


def cmd_flash_cortex(args):
    """CORTEX.bin を W25Q シリーズ SPI Flash (Pre-MVP は W25Q64) に書き込む"""
    from flash_cortex import open_backend, W25Q128, flash_cortex
    import hashlib

    path = args.flash_cortex
    if not Path(path).exists():
        print(f"❌ CORTEX.bin が見つかりません: {path}")
        print("   先に `python cortex_manager.py` でビルドしてください。")
        return 1

    data = Path(path).read_bytes()
    print(f"📥 {path}: {len(data)} bytes "
          f"(SHA256: {hashlib.sha256(data).hexdigest()[:16]}...)")

    backend = open_backend(simulate=args.simulate)
    flash = W25Q128(backend)
    try:
        result = flash_cortex(data, flash, addr=0)
        if result["verified"]:
            print(f"\n✅ CORTEX書き込み成功")
            return 0
        else:
            print(f"\n❌ ベリファイ失敗")
            return 1
    finally:
        flash.close()


def cmd_load_cortex(args):
    """CORTEX.bin (ファイルまたはFlashからdump) を読み込んで中身を表示"""
    from cortex_manager import CortexManager

    path = args.load_cortex
    if not Path(path).exists():
        print(f"❌ CORTEX見つからず: {path}")
        return 1

    mgr = CortexManager()
    try:
        cortex = mgr.load(path)
    except Exception as e:
        print(f"❌ Load failed: {e}")
        return 1

    print(f"✅ CORTEX v{cortex.meta.version} loaded")
    print(f"   created:  {cortex.meta.created_at}")
    print(f"   updated:  {cortex.meta.updated_at}")
    print(f"   checksum: {cortex.meta.checksum[:24]}...")
    print(f"   whisper:  {cortex.whisper.model_file}")
    print(f"   bonsai:   {cortex.bonsai.model_file} ({len(cortex.bonsai.dimensions)}次元)")
    print(f"   minilm:   {cortex.minilm.model_file} ({cortex.minilm.vector_dim}d)")
    print(f"   wp cfg:   max_active={cortex.watchpoint.max_active} "
          f"fitness_floor={cortex.watchpoint.fitness_floor} "
          f"half_life={cortex.watchpoint.decay_half_life_days}d")
    errors = mgr.validate()
    print(f"   validation: {'✅ OK' if not errors else '❌ ' + ', '.join(errors)}")
    return 0


def cmd_mic(args):
    """USBマイクから録音→VAD→Whisper→SOUL更新"""
    from mic_capture import MicCapture, WhisperRunner, VADSession, SpeechSegment
    from soul_cortex import SoulCortex
    from cortex_manager import CortexManager

    # CORTEX読込
    cortex = None
    if args.cortex_path and Path(args.cortex_path).exists():
        try:
            mgr = CortexManager()
            cortex = mgr.load(args.cortex_path)
            print(f"🧠 CORTEX v{cortex.meta.version} loaded from {args.cortex_path}")
        except Exception as e:
            print(f"⚠️  CORTEX load failed: {e} — falling back to defaults")

    # SOUL読込
    soul = load_soul(args.soul_path)
    sc = SoulCortex(soul, cortex=cortex)

    # Whisperセットアップ
    try:
        whisper = WhisperRunner(
            bin_path=args.whisper_bin,
            model_path=args.whisper_model,
            language="ja",
            simulate=args.simulate,
        )
    except FileNotFoundError as e:
        print(f"❌ {e}")
        return 1

    vad = VADSession(aggressiveness=args.vad_aggressiveness)

    # セグメントハンドラ
    segment_count = [0]

    def on_segment(seg: SpeechSegment):
        segment_count[0] += 1
        print(f"\n🎤 [{seg.start_time:.1f}s, {seg.duration_seconds:.1f}s] {seg.text}")

        if not seg.text.strip() or seg.text.startswith("[SIMULATED]") and not args.simulate_ollama:
            # テキストが空、またはSIMULATEモードでOllamaを叩きたくない場合はスキップ
            pass

        # テキストがあればOllamaで抽出
        if seg.text.strip() and not args.simulate_ollama:
            try:
                delta = extract_soul_delta(seg.text, model=args.model)
                update_soul(soul, delta, raw_text=seg.text)
                _print_feedback(delta)
            except Exception as e:
                print(f"⚠️  extraction skipped: {e}")

        # Soul Cortex (3モデル協調) の音響処理
        try:
            sc.process(
                text=seg.text,
                audio_features=seg.to_audio_features(),
            )
        except Exception as e:
            print(f"⚠️  soul_cortex processing skipped: {e}")

        # 保存
        save_soul(soul, args.soul_path)

    mic = MicCapture(
        on_segment=on_segment,
        whisper=whisper,
        vad=vad,
        device=args.device,
        simulate=args.simulate,
    )

    duration = args.duration if args.duration > 0 else None
    label = f"{args.duration}s" if duration else "infinite (Ctrl+C to stop)"
    print(f"🎙️  Recording ({label})...")

    try:
        mic.run(duration_s=duration)
    except KeyboardInterrupt:
        print("\n⏹  Stopped")

    print(f"\n✅ Processed {segment_count[0]} segments")
    # 定期evolve
    if args.evolve_on_exit:
        stats = sc.evolve_watchpoints()
        if stats:
            print(f"🧬 Evolved: culled={len(stats['culled'])}, "
                  f"merged={len(stats['merged'])}, distilled={stats.get('distilled', 0)}")
        save_soul(soul, args.soul_path)
    return 0


def cmd_evolve(args):
    """Watch Point進化サイクルのみ実行"""
    from soul_cortex import SoulCortex
    from cortex_manager import CortexManager

    cortex = None
    if args.cortex_path and Path(args.cortex_path).exists():
        try:
            mgr = CortexManager()
            cortex = mgr.load(args.cortex_path)
        except Exception:
            pass

    soul = load_soul(args.soul_path)
    sc = SoulCortex(soul, cortex=cortex)

    stats = sc.evolve_watchpoints()
    if stats is None:
        print("⚠️  WatchPoint module not available")
        return 1
    print("🧬 WatchPoint Evolution")
    print(f"   transitioned_to_dormant: {stats['transitioned_to_dormant']}")
    print(f"   transitioned_to_dying:   {stats['transitioned_to_dying']}")
    print(f"   culled:                   {stats['culled']}")
    print(f"   merged:                   {stats['merged']}")
    print(f"   distilled:                {stats.get('distilled', 0)}")
    save_soul(soul, args.soul_path)
    return 0


def cmd_input(args):
    """単発テキスト入力"""
    text = args.input
    if not text.strip():
        print("❌ テキストが空です。")
        return

    soul = load_soul(args.soul_path)
    _process_text(soul, text, args)


def cmd_interactive(args):
    """対話モード"""
    print("╔══════════════════════════════════════════╗")
    print("║    Ghost-Printer A1 — SOUL Builder      ║")
    print("║    日記やメモを入力してください          ║")
    print("║    コマンド: /status /soul /quit /help   ║")
    print("╚══════════════════════════════════════════╝")
    print()

    # 接続確認
    status = check_ollama_connection()
    if not status["connected"]:
        print("❌ Ollamaに接続できません。先に `python main.py --check` で確認してください。")
        return

    model_available = any(m.startswith(args.model) for m in status["models"])
    if not model_available:
        print(f"⚠️  モデル '{args.model}' が見つかりません。")
        print(f"   `ollama pull {args.model}` でインストールしてください。")
        return

    print(f"✅ 接続OK — モデル: {args.model}")
    print()

    soul = load_soul(args.soul_path)

    while True:
        try:
            text = input("📝 > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n👋 終了します。")
            break

        if not text:
            continue

        # コマンド処理
        if text.startswith("/"):
            cmd = text.lower().split()[0]
            if cmd in ("/quit", "/exit", "/q"):
                print("👋 終了します。")
                break
            elif cmd in ("/status", "/soul"):
                print()
                print(soul_summary(soul))
                print()
            elif cmd == "/help":
                print("  /status  — SOUL状態表示")
                print("  /soul    — /status と同じ")
                print("  /quit    — 終了")
                print("  /raw     — 直近のraw SOULをJSON表示")
                print("  /help    — このヘルプ")
            elif cmd == "/raw":
                print(json.dumps(soul["core_identity"], ensure_ascii=False, indent=2))
            else:
                print(f"  不明なコマンド: {cmd}")
            continue

        # テキスト処理
        _process_text(soul, text, args)
        soul = load_soul(args.soul_path)  # 最新を再読み込み


def _process_text(soul: dict, text: str, args):
    """テキストを処理してSOULを更新する"""
    # コンテキスト（今はシンプルに日時のみ）
    now = datetime.now()
    hour = now.hour
    if 5 <= hour < 12:
        time_of_day = "朝"
    elif 12 <= hour < 17:
        time_of_day = "昼"
    elif 17 <= hour < 21:
        time_of_day = "夕方"
    else:
        time_of_day = "夜"

    context = {
        "date": now.strftime("%Y-%m-%d"),
        "time_of_day": time_of_day,
    }

    print(f"\n🔄 分析中...")

    try:
        # 1. LLMで抽出
        delta = extract_soul_delta(text, context=context, model=args.model)

        # 2. SOUL更新
        soul = update_soul(soul, delta, raw_text=text, context=context)

        # 3. 保存
        save_soul(soul, args.soul_path)

        # 4. フィードバック表示
        _print_feedback(delta)

    except ConnectionError as e:
        print(f"❌ {e}")
    except ValueError as e:
        print(f"⚠️  抽出結果のパースに失敗: {e}")
    except Exception as e:
        print(f"❌ エラー: {e}")


def _print_feedback(delta: dict):
    """抽出結果のフィードバックを表示する"""
    em = delta["emotion"]
    importance = delta["importance"]
    imp_bar = "▓" * int(importance * 10) + "░" * (10 - int(importance * 10))

    print(f"  ✅ 記録完了")
    print(f"  📊 重要度: [{imp_bar}] {importance:.1f}")
    print(f"  💭 感情: {em['name']} (強度 {em['intensity']:.1f})")

    if delta["personality_signals"]:
        sigs = delta["personality_signals"][:3]
        sig_strs = [f"{s['dimension']}={s['value']:.2f}" for s in sigs]
        print(f"  🧠 性格シグナル: {', '.join(sig_strs)}")

    if delta["summary"]:
        print(f"  📝 要約: {delta['summary']}")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Ghost-Printer A1 — SOUL Builder CLI"
    )
    parser.add_argument("--soul-path", default=DEFAULT_SOUL_PATH,
                        help=f"soul.json のパス (default: {DEFAULT_SOUL_PATH})")
    parser.add_argument("--cortex-path", default=DEFAULT_CORTEX_PATH,
                        help=f"CORTEX.bin のパス (default: {DEFAULT_CORTEX_PATH})")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help=f"Ollamaモデル名 (default: {DEFAULT_MODEL})")

    # 基本モード
    parser.add_argument("--check", action="store_true", help="Ollama接続確認")
    parser.add_argument("--status", action="store_true", help="SOUL状態表示")
    parser.add_argument("--input", "-i", type=str, help="単発テキスト入力")
    parser.add_argument("--init", action="store_true",
                        help="SOULを初期化（既存データは上書き）")

    # 実機モード（Pi 5向け）
    parser.add_argument("--mic", action="store_true",
                        help="USBマイクから録音→SOUL更新")
    parser.add_argument("--duration", type=float, default=60.0,
                        help="--mic 時の録音秒数 (default: 60, 0=infinite)")
    parser.add_argument("--device", type=int, default=None,
                        help="--mic 時のsounddeviceデバイスID")
    parser.add_argument("--vad-aggressiveness", type=int, default=2, choices=[0, 1, 2, 3])
    parser.add_argument("--whisper-bin", default="./whisper.cpp/main")
    parser.add_argument("--whisper-model", default="./whisper.cpp/models/ggml-tiny.bin")
    parser.add_argument("--simulate", action="store_true",
                        help="マイク/SPI/Whisperのシミュレータモード")
    parser.add_argument("--simulate-ollama", action="store_true",
                        help="Ollama呼び出しもスキップ（開発時）")

    parser.add_argument("--flash-cortex", type=str, nargs="?", const=DEFAULT_CORTEX_PATH,
                        help="CORTEX.bin を SPI Flash に焼く (--simulate でシミュレート)")
    parser.add_argument("--load-cortex", type=str, nargs="?", const=DEFAULT_CORTEX_PATH,
                        help="CORTEX.bin を読み込んで内容を表示")
    parser.add_argument("--evolve", action="store_true",
                        help="Watch Point 進化サイクルのみ実行")
    parser.add_argument("--evolve-on-exit", action="store_true",
                        help="--mic 終了時に evolve を自動実行")

    args = parser.parse_args()

    # soul.json の親ディレクトリを作成
    Path(args.soul_path).parent.mkdir(parents=True, exist_ok=True)

    # 初期化
    if args.init:
        soul = create_empty_soul()
        save_soul(soul, args.soul_path)
        print(f"✅ SOULを初期化しました: {args.soul_path}")
        return 0

    # 実機モード分岐
    if args.flash_cortex:
        return cmd_flash_cortex(args)
    if args.load_cortex:
        args.load_cortex = args.load_cortex
        return cmd_load_cortex(args)
    if args.evolve:
        return cmd_evolve(args)
    if args.mic:
        return cmd_mic(args)

    # コマンド分岐
    if args.check:
        cmd_check(args)
    elif args.status:
        cmd_status(args)
    elif args.input:
        cmd_input(args)
    else:
        cmd_interactive(args)
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
