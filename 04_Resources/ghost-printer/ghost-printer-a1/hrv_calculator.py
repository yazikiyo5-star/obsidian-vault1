"""
Ghost-Printer B4 — HRV (Heart Rate Variability) 計算

仕様: specs/b4_colmi_r02_protocol.md §4

純関数ライブラリ。 RR 間隔 (ms) のリストから:
  - mean_hr  : 平均心拍 (BPM)
  - RMSSD    : 副交感神経活性 (高=リラックス)
  - SDNN     : 全体の心拍変動の標準偏差
  - pNN50    : 50ms 以上ジャンプする RR 隣接ペアの割合
  - stress_score : 0..1 のストレス度 (RMSSD ベース)

入力は通常 COLMI R02 の `HeartRateSample.rri_ms` 列だが、 任意の RR 列で動く。

単位は ms。 不正値 (0, 200ms 未満, 2000ms 超) は事前にフィルタすること。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable


# ════════════════════════════════════════════════════════════════════════════
# 個別指標
# ════════════════════════════════════════════════════════════════════════════


def rmssd(rri_ms: Iterable[float]) -> float:
    """RMSSD: 隣接 RR 間隔差の二乗平均平方根。

    式: sqrt(mean( (rri[i+1] - rri[i])^2 ))

    意味:
      - 副交感神経活性の代理指標
      - 高い = リラックス、 低い = ストレス/緊張
      - 安静時の典型値: 20-100 ms
    """
    rri = list(rri_ms)
    if len(rri) < 2:
        return 0.0
    diffs = [rri[i + 1] - rri[i] for i in range(len(rri) - 1)]
    sq = [d * d for d in diffs]
    return math.sqrt(sum(sq) / len(sq))


def sdnn(rri_ms: Iterable[float]) -> float:
    """SDNN: NN (= 正常な RR) 間隔の標準偏差。

    式: sqrt( mean( (rri[i] - mean(rri))^2 ) )  (母標準偏差)

    意味:
      - 自律神経全体の活性度
      - 高い = 心拍変動の幅広さ (健康な兆候)
      - 安静 5 分の典型値: 30-100 ms
    """
    rri = list(rri_ms)
    if len(rri) < 2:
        return 0.0
    mean = sum(rri) / len(rri)
    var = sum((x - mean) ** 2 for x in rri) / len(rri)
    return math.sqrt(var)


def pnn50(rri_ms: Iterable[float]) -> float:
    """pNN50: 隣接 RR 間隔差の絶対値が 50ms を超える割合。

    式: count(|rri[i+1] - rri[i]| > 50) / (N - 1)

    意味:
      - RMSSD の正規化版 (0..1)
      - 安静時の典型値: 0.05 - 0.50
    """
    rri = list(rri_ms)
    if len(rri) < 2:
        return 0.0
    diffs = [abs(rri[i + 1] - rri[i]) for i in range(len(rri) - 1)]
    over = sum(1 for d in diffs if d > 50)
    return over / len(diffs)


def mean_hr(rri_ms: Iterable[float]) -> float:
    """平均心拍 (BPM) を RR 間隔から計算。

    式: 60000 / mean(rri_ms)
    """
    rri = list(rri_ms)
    if not rri:
        return 0.0
    avg_rri = sum(rri) / len(rri)
    if avg_rri <= 0:
        return 0.0
    return 60_000.0 / avg_rri


def stress_score(rmssd_ms: float, *, baseline_rmssd: float = 50.0) -> float:
    """RMSSD からストレススコア (0..1) を線形マッピング。

    - rmssd >= baseline → score = 0.0 (リラックス)
    - rmssd == baseline/2 → score ≈ 0.5
    - rmssd <= 0 → score = 1.0 (高ストレス)

    Args:
        rmssd_ms: RMSSD 値 (ms)
        baseline_rmssd: 個人の安静時 baseline (デフォルト 50ms = 一般成人の中央値)
    """
    if baseline_rmssd <= 0:
        return 1.0
    if rmssd_ms <= 0:
        return 1.0
    raw = 1.0 - (rmssd_ms / baseline_rmssd)
    return max(0.0, min(1.0, raw))


# ════════════════════════════════════════════════════════════════════════════
# まとめて計算
# ════════════════════════════════════════════════════════════════════════════


@dataclass
class HrvMetrics:
    sample_count: int
    mean_hr: float
    rmssd_ms: float
    sdnn_ms: float
    pnn50: float
    stress_score: float
    baseline_rmssd: float = 50.0

    def to_dict(self) -> dict:
        return {
            "sample_count": self.sample_count,
            "hr_avg": round(self.mean_hr, 1),
            "rmssd_ms": round(self.rmssd_ms, 1),
            "sdnn_ms": round(self.sdnn_ms, 1),
            "pnn50": round(self.pnn50, 3),
            "stress_level": round(self.stress_score, 3),
            "baseline_rmssd": self.baseline_rmssd,
        }


def compute_hrv(
    rri_ms: Iterable[float],
    *,
    baseline_rmssd: float = 50.0,
) -> HrvMetrics:
    """RR 間隔列から全 HRV 指標を一括計算。"""
    rri_list = [float(x) for x in rri_ms]
    rmssd_v = rmssd(rri_list)
    return HrvMetrics(
        sample_count=len(rri_list),
        mean_hr=mean_hr(rri_list),
        rmssd_ms=rmssd_v,
        sdnn_ms=sdnn(rri_list),
        pnn50=pnn50(rri_list),
        stress_score=stress_score(rmssd_v, baseline_rmssd=baseline_rmssd),
        baseline_rmssd=baseline_rmssd,
    )


# ════════════════════════════════════════════════════════════════════════════
# 入力フィルタ (実機データのノイズ除去)
# ════════════════════════════════════════════════════════════════════════════


def filter_rri(
    rri_ms: Iterable[float],
    *,
    min_ms: float = 300.0,
    max_ms: float = 2000.0,
    max_jump_ratio: float = 0.20,
) -> list[float]:
    """生 RR 列から外れ値を除去する。

    - min_ms / max_ms 範囲外を除外 (= 心拍 30..200 bpm 相当)
    - 隣接サンプル比 max_jump_ratio 超を除外 (= センサーアーティファクト)
    """
    out: list[float] = []
    last: float | None = None
    for x in rri_ms:
        x = float(x)
        if x < min_ms or x > max_ms:
            continue
        if last is not None:
            jump = abs(x - last) / last
            if jump > max_jump_ratio:
                continue
        out.append(x)
        last = x
    return out
