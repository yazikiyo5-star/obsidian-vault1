#!/usr/bin/env python3
"""
Ghost-Printer — Microphone Capture + VAD + Whisper.cpp

USBマイクから音声を取得し、VAD（Voice Activity Detection）で発話区間を切り出し、
whisper.cpp に渡して日本語テキストと韻律特徴を抽出する。

アーキテクチャ:
  [USB Mic] → sounddevice → 16kHz mono PCM ring buffer
             → webrtcvad で 30ms フレーム毎に speech/silence 判定
             → speech区間をセグメント化 (min_duration〜max_duration)
             → WAV書き出し → whisper.cpp 呼び出し
             → テキスト + 韻律特徴 (pitch/tempo/energy) を返す

依存:
  sounddevice (portaudio)
  webrtcvad
  numpy
  whisper.cpp (外部バイナリ)

シミュレータモード:
  --simulate で実マイク/whisperがなくてもダミー音声で動作確認可能
"""

import argparse
import subprocess
import tempfile
import wave
import struct
import time
import os
import sys
import json
import math
from pathlib import Path
from typing import Optional, List, Callable, Dict, Any
from dataclasses import dataclass, field


# ════════════════════════════════════════════════════════════════════════════════
# 設定
# ════════════════════════════════════════════════════════════════════════════════

SAMPLE_RATE = 16000        # Whisper推奨 (16kHz mono)
CHANNELS = 1
SAMPLE_WIDTH = 2           # int16 = 2 bytes
VAD_FRAME_MS = 30          # webrtcvadは10/20/30msのみ対応
VAD_FRAME_SIZE = int(SAMPLE_RATE * VAD_FRAME_MS / 1000)  # 480 samples

DEFAULT_MIN_SEGMENT_MS = 1000    # 1秒未満は無視
DEFAULT_MAX_SEGMENT_MS = 30000   # 30秒で強制切断
DEFAULT_SILENCE_TAIL_MS = 800    # 末尾の無音でセグメント終端判定


# ════════════════════════════════════════════════════════════════════════════════
# データ構造
# ════════════════════════════════════════════════════════════════════════════════

@dataclass
class SpeechSegment:
    """VADで切り出された発話区間"""
    pcm: bytes                         # int16 mono PCM
    sample_rate: int = SAMPLE_RATE
    duration_seconds: float = 0.0
    start_time: float = 0.0            # 録音開始からの秒数
    # 韻律特徴（analyze_segmentで計算）
    pitch_mean: float = 0.0
    pitch_variance: float = 0.0
    energy_mean: float = 0.0
    energy_variance: float = 0.0
    tempo: float = 0.5                 # 話速プロキシ
    pause_ratio: float = 0.0           # セグメント内のVAD非発話率
    speech_ratio: float = 1.0          # 内部発話率（セグメント単位ではほぼ1）
    # Whisper結果
    text: str = ""

    def to_audio_features(self) -> dict:
        """Soul Cortex.process_acoustic() が期待する形式に変換"""
        return {
            "pitch_mean": self.pitch_mean,
            "pitch_variance": self.pitch_variance,
            "tempo": self.tempo,
            "pause_ratio": self.pause_ratio,
            "energy_mean": self.energy_mean,
            "energy_variance": self.energy_variance,
            "duration": self.duration_seconds,
            "speech_ratio": self.speech_ratio,
        }


# ════════════════════════════════════════════════════════════════════════════════
# VAD (Voice Activity Detection)
# ════════════════════════════════════════════════════════════════════════════════

class VADSession:
    """
    webrtcvad をラップして、ストリーム入力から発話区間を切り出す。

    使い方:
      vad = VADSession(aggressiveness=2)
      for frame in stream_frames(audio):
          segment = vad.feed(frame)
          if segment is not None:
              # 発話区間完成
              handle_segment(segment)
    """

    def __init__(
        self,
        aggressiveness: int = 2,
        min_segment_ms: int = DEFAULT_MIN_SEGMENT_MS,
        max_segment_ms: int = DEFAULT_MAX_SEGMENT_MS,
        silence_tail_ms: int = DEFAULT_SILENCE_TAIL_MS,
    ):
        try:
            import webrtcvad  # type: ignore
            self.vad = webrtcvad.Vad(aggressiveness)
            self._native = True
        except ImportError:
            # webrtcvad が無い環境ではフォールバック（RMS閾値）
            self.vad = None
            self._native = False
            self._rms_threshold = 500  # int16スケール

        self.min_samples = int(SAMPLE_RATE * min_segment_ms / 1000)
        self.max_samples = int(SAMPLE_RATE * max_segment_ms / 1000)
        self.silence_frames_to_end = int(silence_tail_ms / VAD_FRAME_MS)

        self._buffer: List[bytes] = []
        self._in_speech = False
        self._silence_frames = 0
        self._samples_accumulated = 0

    def _is_speech(self, frame: bytes) -> bool:
        if self._native:
            return self.vad.is_speech(frame, SAMPLE_RATE)
        # フォールバック: RMSエネルギー閾値
        samples = struct.unpack(f"<{len(frame)//2}h", frame)
        rms = math.sqrt(sum(s * s for s in samples) / len(samples)) if samples else 0
        return rms > self._rms_threshold

    def feed(self, frame: bytes) -> Optional[SpeechSegment]:
        """
        30msフレームを投入。セグメントが完成したら SpeechSegment を返す。
        完成していなければ None。
        """
        assert len(frame) == VAD_FRAME_SIZE * SAMPLE_WIDTH, \
            f"Frame size must be {VAD_FRAME_SIZE * SAMPLE_WIDTH}, got {len(frame)}"

        is_speech = self._is_speech(frame)

        if is_speech:
            self._buffer.append(frame)
            self._samples_accumulated += VAD_FRAME_SIZE
            self._silence_frames = 0
            self._in_speech = True

            # 最大長強制切断
            if self._samples_accumulated >= self.max_samples:
                return self._finalize()
        else:
            if self._in_speech:
                # 発話中の無音 → 末尾保持のためbufferに追加
                self._buffer.append(frame)
                self._samples_accumulated += VAD_FRAME_SIZE
                self._silence_frames += 1

                if self._silence_frames >= self.silence_frames_to_end:
                    return self._finalize()

        return None

    def _finalize(self) -> Optional[SpeechSegment]:
        if self._samples_accumulated < self.min_samples:
            self._reset()
            return None
        pcm = b"".join(self._buffer)
        duration = self._samples_accumulated / SAMPLE_RATE
        seg = SpeechSegment(
            pcm=pcm,
            sample_rate=SAMPLE_RATE,
            duration_seconds=duration,
        )
        self._reset()
        return seg

    def _reset(self):
        self._buffer = []
        self._in_speech = False
        self._silence_frames = 0
        self._samples_accumulated = 0

    def flush(self) -> Optional[SpeechSegment]:
        """ストリーム終端時に呼ぶ。蓄積中の区間を強制的に完成させる"""
        if self._samples_accumulated >= self.min_samples:
            return self._finalize()
        self._reset()
        return None


# ════════════════════════════════════════════════════════════════════════════════
# 韻律特徴抽出
# ════════════════════════════════════════════════════════════════════════════════

def analyze_segment(seg: SpeechSegment) -> None:
    """
    セグメントの韻律特徴をin-placeで計算する。

    簡易実装:
      - pitch: Zero-Crossing Rate を擬似ピッチとして利用（正規化）
      - energy: RMS
      - tempo: 0.5固定（将来 text と duration から計算）
      - pause_ratio: VAD非発話フレーム比率（近似）
    """
    try:
        import numpy as np
    except ImportError:
        # numpy無しでもminimalに動作
        seg.pitch_mean = 0.5
        seg.energy_mean = 0.5
        seg.tempo = 0.5
        return

    pcm = seg.pcm
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    if len(samples) == 0:
        return

    # RMS energy（windowed）
    window = int(SAMPLE_RATE * 0.03)  # 30msウィンドウ
    n_windows = max(1, len(samples) // window)
    energies = []
    zcrs = []
    for i in range(n_windows):
        chunk = samples[i * window: (i + 1) * window]
        if len(chunk) == 0:
            continue
        rms = float(np.sqrt(np.mean(chunk ** 2)))
        energies.append(rms)
        # Zero crossing rate
        zc = int(np.sum(np.abs(np.diff(np.sign(chunk))) > 0))
        zcrs.append(zc / len(chunk) if len(chunk) > 0 else 0)

    if energies:
        seg.energy_mean = float(np.clip(np.mean(energies) * 4.0, 0, 1))  # 経験的スケーリング
        seg.energy_variance = float(np.clip(np.var(energies) * 20.0, 0, 1))
    if zcrs:
        # ZCRをピッチの代理として正規化
        seg.pitch_mean = float(np.clip(np.mean(zcrs) * 5.0, 0, 1))
        seg.pitch_variance = float(np.clip(np.var(zcrs) * 50.0, 0, 1))

    # tempo/pause は現状ヒューリスティック
    seg.tempo = 0.5
    # 低エネルギー窓の割合を pause_ratio とする
    if energies:
        threshold = np.mean(energies) * 0.3
        seg.pause_ratio = float(np.mean([1.0 if e < threshold else 0.0 for e in energies]))


# ════════════════════════════════════════════════════════════════════════════════
# Whisper.cpp 呼び出し
# ════════════════════════════════════════════════════════════════════════════════

class WhisperRunner:
    """
    whisper.cpp の `main` バイナリを subprocess で呼ぶ薄いラッパー。

    依存:
      - whisper.cpp のビルド済み `main` 実行ファイル
      - ggml-*.bin モデル

    シミュレータモードでは固定テキストを返す。
    """

    def __init__(
        self,
        bin_path: str = "./whisper.cpp/main",
        model_path: str = "./whisper.cpp/models/ggml-tiny.bin",
        language: str = "ja",
        simulate: bool = False,
    ):
        self.bin_path = bin_path
        self.model_path = model_path
        self.language = language
        self.simulate = simulate
        if not simulate:
            if not Path(bin_path).exists():
                raise FileNotFoundError(
                    f"whisper.cpp binary not found: {bin_path}\n"
                    f"ビルド方法: git clone https://github.com/ggerganov/whisper.cpp && cd whisper.cpp && make -j4"
                )
            if not Path(model_path).exists():
                raise FileNotFoundError(
                    f"Whisper model not found: {model_path}\n"
                    f"DL方法: cd whisper.cpp && bash ./models/download-ggml-model.sh tiny"
                )

    def transcribe(self, pcm: bytes, sample_rate: int = SAMPLE_RATE) -> str:
        if self.simulate:
            # シミュレータ: 固定テキスト
            return "[SIMULATED] 今日は新しいプロジェクトに取り組んで、とても充実していた。"

        # WAVファイルに書き出し
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = tmp.name
            _write_wav(tmp_path, pcm, sample_rate)

        try:
            cmd = [
                self.bin_path,
                "-m", self.model_path,
                "-l", self.language,
                "-f", tmp_path,
                "-nt",       # no timestamps
                "--no-prints",
                "-otxt",     # output text file
            ]
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=60,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    f"whisper.cpp failed (rc={result.returncode}): {result.stderr}"
                )
            # whisper.cpp は <wav>.txt に出力する
            txt_path = tmp_path + ".txt"
            text = ""
            if Path(txt_path).exists():
                text = Path(txt_path).read_text(encoding="utf-8").strip()
                os.unlink(txt_path)
            else:
                # stdoutにある場合はそれを使う
                text = result.stdout.strip()
            return text
        finally:
            if Path(tmp_path).exists():
                os.unlink(tmp_path)


def _write_wav(path: str, pcm: bytes, sample_rate: int):
    with wave.open(path, "wb") as w:
        w.setnchannels(CHANNELS)
        w.setsampwidth(SAMPLE_WIDTH)
        w.setframerate(sample_rate)
        w.writeframes(pcm)


# ════════════════════════════════════════════════════════════════════════════════
# マイク録音（メインループ）
# ════════════════════════════════════════════════════════════════════════════════

class MicCapture:
    """
    マイクから連続録音しながら、VADで発話区間を切り出して処理する。

    使い方:
      mic = MicCapture(on_segment=handle_segment, whisper=WhisperRunner(...))
      mic.run(duration_s=60)  # 60秒録音

      # または無限ループ（systemd常駐用）
      mic.run()
    """

    def __init__(
        self,
        on_segment: Callable[[SpeechSegment], None],
        whisper: Optional[WhisperRunner] = None,
        vad: Optional[VADSession] = None,
        device: Optional[int] = None,
        simulate: bool = False,
    ):
        self.on_segment = on_segment
        self.whisper = whisper or WhisperRunner(simulate=simulate)
        self.vad = vad or VADSession()
        self.device = device
        self.simulate = simulate
        self._running = False
        self._t0 = 0.0

    def run(self, duration_s: Optional[float] = None) -> int:
        """
        録音ループ。duration_s=None で無限ループ。
        Returns:
            処理したセグメント数
        """
        self._t0 = time.time()
        self._running = True
        count = 0

        if self.simulate:
            # シミュレータ: ダミーPCMを投入
            count = self._run_simulated(duration_s or 2.0)
        else:
            count = self._run_real(duration_s)

        self._running = False
        return count

    def stop(self) -> None:
        self._running = False

    # ── 実マイク ──

    def _run_real(self, duration_s: Optional[float]) -> int:
        try:
            import sounddevice as sd  # type: ignore
        except ImportError:
            raise RuntimeError(
                "sounddevice が必要です: pip install sounddevice\n"
                "または --simulate を使ってください。"
            )

        count = 0
        frame_bytes = VAD_FRAME_SIZE * SAMPLE_WIDTH

        def callback(indata, frames, time_info, status):
            nonlocal count
            if status:
                print(f"⚠️  audio status: {status}", file=sys.stderr)
            # int16 変換
            import numpy as np
            pcm = (indata[:, 0] * 32767).astype(np.int16).tobytes()
            # VADフレームサイズに分割
            for i in range(0, len(pcm), frame_bytes):
                frame = pcm[i:i + frame_bytes]
                if len(frame) < frame_bytes:
                    continue  # 端数は捨てる
                seg = self.vad.feed(frame)
                if seg:
                    seg.start_time = time.time() - self._t0
                    count += self._handle_segment(seg)

        with sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="float32",
            blocksize=VAD_FRAME_SIZE,
            device=self.device,
            callback=callback,
        ):
            deadline = time.time() + duration_s if duration_s else None
            while self._running:
                time.sleep(0.1)
                if deadline and time.time() >= deadline:
                    break

        # ストリーム終端
        trailing = self.vad.flush()
        if trailing:
            trailing.start_time = time.time() - self._t0
            count += self._handle_segment(trailing)

        return count

    # ── シミュレータ ──

    def _run_simulated(self, duration_s: float) -> int:
        """ダミーPCM (正弦波 + ノイズ) を注入して1セグメント生成"""
        import math
        n = int(SAMPLE_RATE * duration_s)
        amplitude = 8000
        freq = 220  # Hz
        frames = bytearray()
        for i in range(n):
            v = int(amplitude * math.sin(2 * math.pi * freq * i / SAMPLE_RATE))
            frames.extend(struct.pack("<h", v))

        frame_bytes = VAD_FRAME_SIZE * SAMPLE_WIDTH
        count = 0
        for i in range(0, len(frames), frame_bytes):
            frame = bytes(frames[i:i + frame_bytes])
            if len(frame) < frame_bytes:
                break
            seg = self.vad.feed(frame)
            if seg:
                seg.start_time = i / (SAMPLE_RATE * SAMPLE_WIDTH)
                count += self._handle_segment(seg)

        trailing = self.vad.flush()
        if trailing:
            count += self._handle_segment(trailing)
        return count

    def _handle_segment(self, seg: SpeechSegment) -> int:
        # 韻律解析
        analyze_segment(seg)
        # Whisperで書き起こし
        try:
            seg.text = self.whisper.transcribe(seg.pcm, seg.sample_rate)
        except Exception as e:
            print(f"⚠️  Whisper transcription failed: {e}", file=sys.stderr)
            seg.text = ""
        # コールバック
        self.on_segment(seg)
        return 1


# ════════════════════════════════════════════════════════════════════════════════
# CLI
# ════════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Ghost-Printer Mic Capture + VAD + Whisper",
    )
    parser.add_argument("--duration", type=float, default=30.0,
                        help="録音秒数 (default: 30, 0で無限)")
    parser.add_argument("--simulate", action="store_true",
                        help="シミュレータモード（実マイク/whisper不要）")
    parser.add_argument("--whisper-bin", default="./whisper.cpp/main")
    parser.add_argument("--whisper-model", default="./whisper.cpp/models/ggml-tiny.bin")
    parser.add_argument("--language", default="ja")
    parser.add_argument("--device", type=int, default=None,
                        help="sounddevice のデバイスID (未指定でデフォルト)")
    parser.add_argument("--vad-aggressiveness", type=int, default=2, choices=[0, 1, 2, 3])
    parser.add_argument("--min-ms", type=int, default=DEFAULT_MIN_SEGMENT_MS)
    parser.add_argument("--max-ms", type=int, default=DEFAULT_MAX_SEGMENT_MS)

    args = parser.parse_args()

    def print_segment(seg: SpeechSegment):
        print(f"\n🎤 [{seg.start_time:.1f}s, {seg.duration_seconds:.1f}s] {seg.text}")
        print(f"   pitch={seg.pitch_mean:.2f} energy={seg.energy_mean:.2f} "
              f"tempo={seg.tempo:.2f} pause={seg.pause_ratio:.2f}")

    try:
        whisper = WhisperRunner(
            bin_path=args.whisper_bin,
            model_path=args.whisper_model,
            language=args.language,
            simulate=args.simulate,
        )
    except FileNotFoundError as e:
        print(f"❌ {e}")
        return 1

    vad = VADSession(
        aggressiveness=args.vad_aggressiveness,
        min_segment_ms=args.min_ms,
        max_segment_ms=args.max_ms,
    )

    mic = MicCapture(
        on_segment=print_segment,
        whisper=whisper,
        vad=vad,
        device=args.device,
        simulate=args.simulate,
    )

    duration = args.duration if args.duration > 0 else None
    label = f"{args.duration}s" if duration else "infinite"
    print(f"🎙️  Recording ({label})... Ctrl+C to stop")

    try:
        count = mic.run(duration_s=duration)
        print(f"\n✅ Captured {count} segments")
        return 0
    except KeyboardInterrupt:
        print("\n⏹  Stopped by user")
        return 0


if __name__ == "__main__":
    sys.exit(main())
