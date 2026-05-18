"""
Ghost-Printer A7 — 適応型 Watch Point: LLM ベースの提案者 + ハイブリッドポリシー

仕様: specs/a7_adaptive_watchpoint.md

設計方針:
  - LLM 呼出はコーラブルを依存性注入 (httpx 等の遷延依存をこのモジュールに
    持ち込まない、 テストに mock を渡せる)
  - 既存 watchpoint.py / WatchPointManager / WatchPointRules は変更しない
  - LLM が応答しなくても (HTTPエラー / JSON 壊れ) 落ちずにルール経路で継続
  - 提案後の dedup / 容量管理は WatchPointManager.propose() に委ねる
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any, Callable, Iterable

from watchpoint import (
    WatchPoint,
    WatchPointManager,
    WatchPointRules,
    WPTrigger,
)

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════════════
# 採択された性格次元集合 (soul_schema.py:create_empty_soul と整合)
# ════════════════════════════════════════════════════════════════════════════

KNOWN_DIMENSIONS = {
    "openness",
    "conscientiousness",
    "extraversion",
    "agreeableness",
    "neuroticism",
    "curiosity",
    "creativity",
    "empathy",
    "risk_tolerance",
    "independence",
}


# ════════════════════════════════════════════════════════════════════════════
# 提案構造体
# ════════════════════════════════════════════════════════════════════════════


@dataclass
class LlmProposal:
    """LLM が出した 1 件の提案"""

    target: str                          # snake_case, 30 char 以内
    description: str                     # 60 char 以内 (日本語)
    priority: float                      # 0.3 .. 0.9 にクランプ
    affects_dimensions: list[str] = field(default_factory=list)
    rationale: str = ""                  # LLM の理由付け (デバッグ用)
    prior_mu: float = 0.5
    prior_sigma: float = 0.30

    def to_propose_kwargs(self) -> dict:
        """WatchPointManager.propose() に渡す形式へ変換"""
        # description に rationale を埋め込む (Q5 採択)
        desc = self.description
        if self.rationale:
            desc = f"{desc} | {self.rationale}"[:200]
        return {
            "target": self.target,
            "trigger": WPTrigger.LLM_SUGGESTED,
            "priority": self.priority,
            "description": desc,
            "prior_mu": self.prior_mu,
            "prior_sigma": self.prior_sigma,
            "affects_dimensions": list(self.affects_dimensions),
        }


# ════════════════════════════════════════════════════════════════════════════
# プロンプト組み立てとパース
# ════════════════════════════════════════════════════════════════════════════


def _format_core_identity(core: dict, top_n: int = 6) -> str:
    """μ ± σ の表記。 σ 小さい (= 確信度が高い) ものを優先表示。"""
    if not core:
        return "  (まだ観測なし)"
    items = []
    for dim, dist in core.items():
        if not isinstance(dist, dict) or "mu" not in dist:
            continue
        items.append((dim, dist["mu"], dist["sigma"]))
    items.sort(key=lambda x: x[2])  # σ 昇順
    lines = []
    for dim, mu, sigma in items[:top_n]:
        lines.append(f"  {dim}: {mu:.2f} ± {sigma:.2f}")
    return "\n".join(lines)


def _format_top_topics(semantic_map: dict, top_n: int = 5) -> str:
    interests = semantic_map.get("interests", {})
    if not interests:
        return "  (なし)"
    top = sorted(interests.items(), key=lambda x: -x[1])[:top_n]
    return "  " + ", ".join(f"{t}" for t, _ in top)


def _format_top_values(semantic_map: dict, top_n: int = 3) -> str:
    values = semantic_map.get("values", {})
    if not values:
        return "  (なし)"
    top = sorted(values.items(), key=lambda x: -x[1])[:top_n]
    return "  " + ", ".join(f"{v}" for v, _ in top)


def _format_existing_wps(wps: Iterable[WatchPoint], top_n: int = 6) -> str:
    wps_list = [w for w in wps if w.state.value not in ("culled",)]
    if not wps_list:
        return "  (なし)"
    lines = []
    for wp in wps_list[:top_n]:
        lines.append(
            f"  - {wp.target} (priority={wp.priority:.2f}, "
            f"hits={wp.hit_count}/{wp.observation_count})"
        )
    return "\n".join(lines)


def _format_recent_episodes(soul: dict, top_n: int = 5) -> str:
    eps = soul.get("episodic_memory", {}).get("recent", [])
    if not eps:
        return "  (なし)"
    lines = []
    for ep in eps[-top_n:]:
        ts = (ep.get("timestamp") or "")[:10]
        importance = ep.get("importance", 0)
        em = ep.get("emotion") or {}
        em_name = em.get("name") if isinstance(em, dict) else str(em)
        text = ep.get("summary") or ep.get("raw_text", "")
        text = (text or "")[:60]
        lines.append(
            f"  [{ts}] {text} (importance={importance:.2f}, emotion={em_name})"
        )
    return "\n".join(lines)


def build_prompt(
    soul: dict,
    existing_wps: list[WatchPoint],
    *,
    max_new: int = 3,
    extra_directives: str | None = None,
) -> str:
    """LLM に渡すプロンプトを組み立てる"""
    core = soul.get("core_identity", {})
    sem = soul.get("semantic_map", {})

    parts = [
        "あなたは Ghost-Printer の Watch Point 提案者です。",
        "SOUL の最近の動きから、 追跡すべき新しい観測対象を提案してください。",
        "",
        "## 現在の SOUL 状態",
        "性格 (μ ± σ):",
        _format_core_identity(core),
        "",
        "最近のトピック (上位5):",
        _format_top_topics(sem),
        "",
        "最近の価値観:",
        _format_top_values(sem),
        "",
        "## 現在の Watch Points (target が重複しないこと)",
        _format_existing_wps(existing_wps),
        "",
        "## 最近のエピソード",
        _format_recent_episodes(soul),
        "",
        "## 提案ガイドライン",
        f"- 既存 WP と target が重複しないものを最大 {max_new} 件",
        f"- 性格次元のいずれかと結びつく観測対象 ({', '.join(sorted(KNOWN_DIMENSIONS))})",
        "- target は短い英小文字スラッグ (snake_case, 30 文字以内)",
        "- priority は 0.3-0.9",
        "- description は 60 文字以内の日本語",
    ]
    if extra_directives:
        parts.append(extra_directives)

    parts.extend([
        "",
        "## 出力フォーマット (JSON 配列のみ、 説明文不要)",
        "```json",
        '[',
        '  {',
        '    "target": "weekend_socializing",',
        '    "description": "週末に社交イベントが増える兆候",',
        '    "priority": 0.6,',
        '    "affects_dimensions": ["extraversion", "agreeableness"],',
        '    "rationale": "平日は内向的、 週末のエピソードに meet トピックが集中"',
        '  }',
        ']',
        "```",
    ])
    return "\n".join(parts)


_JSON_BLOCK_RE = re.compile(
    r"```(?:json|JSON)?\s*(\[.*?\])\s*```",
    re.DOTALL,
)
_JSON_ARRAY_RE = re.compile(r"(\[\s*\{.*?\}\s*\])", re.DOTALL)


def parse_proposals(
    response_text: str,
    *,
    existing_targets: set[str] | None = None,
    recent_targets: set[str] | None = None,
) -> list[LlmProposal]:
    """LLM 応答から提案候補を抽出。 失敗時は空リスト。

    Args:
        existing_targets: 既存 WP の target セット (重複排除)
        recent_targets: 直近に提案/淘汰された target セット (再提案抑制)
    """
    existing_targets = existing_targets or set()
    recent_targets = recent_targets or set()

    raw_json: str | None = None
    # まずコードブロックを探す
    m = _JSON_BLOCK_RE.search(response_text)
    if m:
        raw_json = m.group(1)
    else:
        m = _JSON_ARRAY_RE.search(response_text)
        if m:
            raw_json = m.group(1)
    if not raw_json:
        return []

    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []

    out: list[LlmProposal] = []
    seen_targets: set[str] = set()
    for entry in data:
        if not isinstance(entry, dict):
            continue
        prop = _coerce_entry(entry)
        if prop is None:
            continue
        if prop.target in existing_targets or prop.target in recent_targets:
            continue
        if prop.target in seen_targets:
            continue
        seen_targets.add(prop.target)
        out.append(prop)
    return out


def _normalize_target(raw: str) -> str:
    s = raw.lower().strip()
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^a-z0-9_]", "", s)
    return s[:30]


def _coerce_entry(entry: dict) -> LlmProposal | None:
    target = entry.get("target")
    description = entry.get("description")
    priority = entry.get("priority")

    if not isinstance(target, str) or not target.strip():
        return None
    if not isinstance(description, str) or not description.strip():
        return None
    if not isinstance(priority, (int, float)):
        return None

    target = _normalize_target(target)
    if not target:
        return None
    description = description.strip()[:120]
    priority = float(priority)
    # クランプ
    priority = max(0.3, min(0.9, priority))

    affects = entry.get("affects_dimensions") or []
    if not isinstance(affects, list):
        affects = []
    affects = [d for d in affects if isinstance(d, str) and d in KNOWN_DIMENSIONS]

    rationale = entry.get("rationale") or ""
    if not isinstance(rationale, str):
        rationale = ""
    rationale = rationale.strip()[:180]

    prior_mu = entry.get("prior_mu", 0.5)
    prior_sigma = entry.get("prior_sigma", 0.30)
    if not isinstance(prior_mu, (int, float)):
        prior_mu = 0.5
    if not isinstance(prior_sigma, (int, float)):
        prior_sigma = 0.30
    prior_mu = max(0.0, min(1.0, float(prior_mu)))
    prior_sigma = max(0.05, min(0.5, float(prior_sigma)))

    return LlmProposal(
        target=target,
        description=description,
        priority=priority,
        affects_dimensions=affects,
        rationale=rationale,
        prior_mu=prior_mu,
        prior_sigma=prior_sigma,
    )


# ════════════════════════════════════════════════════════════════════════════
# LLM 健康状態 (永久死亡からの自己治癒のため)
# ════════════════════════════════════════════════════════════════════════════


class LlmHealthState(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"   # 連続失敗だが回復の可能性あり
    DEAD = "dead"            # 長期停止。 通常呼出は skip し probe で復帰判定


@dataclass
class LlmHealthTracker:
    """LLM の健康状態を追跡する。

    状態遷移:
        HEALTHY ──(連続 N 失敗)──▶ DEGRADED ──(M 時間 成功なし)──▶ DEAD
            ▲                          │                              │
            └──────(任意の成功で復帰)──┴──────────────────────────────┘

    閾値:
        - degraded_threshold: 連続失敗回数。 default=3 (= 3 サイクル)
        - dead_threshold_hours: 直近成功からの経過時間。 default=24h
        - 一度も成功していない場合は created_at が基準

    タイム関数は `datetime.now(timezone.utc)` を使う。 テストでは created_at
    や last_success_at を直接書き換えれば過去/未来を擬似できる。
    """

    state: LlmHealthState = LlmHealthState.HEALTHY
    consecutive_failures: int = 0
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    last_failure_reason: str = ""
    total_invocations: int = 0
    total_failures: int = 0
    state_changed_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    # 閾値 (実機チューニング想定で外から差し替え可能)
    degraded_threshold: int = 3
    dead_threshold_hours: float = 24.0

    # ── 内部 ──

    @staticmethod
    def _utcnow() -> datetime:
        return datetime.now(timezone.utc)

    def _set_state(self, new_state: LlmHealthState, reason: str = "") -> None:
        if new_state == self.state:
            return
        prev = self.state
        self.state = new_state
        self.state_changed_at = self._utcnow()
        if new_state == LlmHealthState.HEALTHY:
            logger.info(f"LLM health: {prev.value} → healthy")
        else:
            logger.warning(
                f"LLM health: {prev.value} → {new_state.value} ({reason or 'state escalated'})"
            )

    def _compute_state_after_failure(self) -> LlmHealthState:
        if self.consecutive_failures < self.degraded_threshold:
            return self.state  # 維持
        # 連続失敗閾値超 → DEGRADED 以上
        reference = self.last_success_at or self.created_at
        hours_since = (self._utcnow() - reference).total_seconds() / 3600
        if hours_since >= self.dead_threshold_hours:
            return LlmHealthState.DEAD
        return LlmHealthState.DEGRADED

    # ── 公開API ──

    def record_success(self) -> None:
        """LLM 呼出が成功したことを記録。 任意の状態から HEALTHY 復帰。"""
        self.consecutive_failures = 0
        self.last_success_at = self._utcnow()
        self.total_invocations += 1
        self._set_state(LlmHealthState.HEALTHY)

    def record_failure(self, reason: str = "") -> None:
        """LLM 呼出が失敗したことを記録。 連続失敗カウンタを進めて状態評価。"""
        self.consecutive_failures += 1
        self.last_failure_at = self._utcnow()
        self.last_failure_reason = (reason or "unknown")[:160]
        self.total_invocations += 1
        self.total_failures += 1
        new_state = self._compute_state_after_failure()
        self._set_state(new_state, reason=self.last_failure_reason)

    def is_dead(self) -> bool:
        return self.state == LlmHealthState.DEAD

    def is_healthy(self) -> bool:
        return self.state == LlmHealthState.HEALTHY

    def status_summary(self) -> dict:
        """main.py --status で表示できる辞書"""
        rate = self.total_failures / max(self.total_invocations, 1)
        return {
            "state": self.state.value,
            "consecutive_failures": self.consecutive_failures,
            "last_success_at": (
                self.last_success_at.isoformat() if self.last_success_at else None
            ),
            "last_failure_at": (
                self.last_failure_at.isoformat() if self.last_failure_at else None
            ),
            "last_failure_reason": self.last_failure_reason,
            "total_invocations": self.total_invocations,
            "total_failures": self.total_failures,
            "failure_rate": round(rate, 3),
            "state_changed_at": self.state_changed_at.isoformat(),
        }

    def to_dict(self) -> dict:
        d = self.status_summary()
        d["created_at"] = self.created_at.isoformat()
        d["degraded_threshold"] = self.degraded_threshold
        d["dead_threshold_hours"] = self.dead_threshold_hours
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "LlmHealthTracker":
        def _parse_dt(s: str | None) -> datetime | None:
            if not s:
                return None
            try:
                return datetime.fromisoformat(s)
            except ValueError:
                return None

        t = cls()
        t.state = LlmHealthState(d.get("state", "healthy"))
        t.consecutive_failures = int(d.get("consecutive_failures", 0))
        t.last_success_at = _parse_dt(d.get("last_success_at"))
        t.last_failure_at = _parse_dt(d.get("last_failure_at"))
        t.last_failure_reason = str(d.get("last_failure_reason", ""))
        t.total_invocations = int(d.get("total_invocations", 0))
        t.total_failures = int(d.get("total_failures", 0))
        t.state_changed_at = _parse_dt(d.get("state_changed_at")) or t._utcnow()
        t.created_at = _parse_dt(d.get("created_at")) or t._utcnow()
        t.degraded_threshold = int(d.get("degraded_threshold", 3))
        t.dead_threshold_hours = float(d.get("dead_threshold_hours", 24.0))
        return t


# ════════════════════════════════════════════════════════════════════════════
# LlmWpProposer
# ════════════════════════════════════════════════════════════════════════════


@dataclass
class _ProposalRecord:
    target: str
    proposed_at: datetime


class LlmWpProposer:
    """LLM ベースの Watch Point 提案者。

    LLM 呼出は `llm_call: Callable[[str], str]` を注入する。
    本番では Ollama / Bonsai のラッパを、 テストでは mock を渡す。
    """

    def __init__(
        self,
        llm_call: Callable[[str], str],
        *,
        recent_suppress_days: float = 7.0,
        recent_history: int = 30,
        health: LlmHealthTracker | None = None,
        skip_when_dead: bool = True,
    ):
        self.llm_call = llm_call
        self.recent_suppress_days = recent_suppress_days
        self.recent_history = recent_history
        self._recent: list[_ProposalRecord] = []
        self.last_invocation_at: datetime | None = None
        self.last_response: str = ""
        self.health = health or LlmHealthTracker()
        self.skip_when_dead = skip_when_dead

    def _recent_targets(self) -> set[str]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.recent_suppress_days)
        return {r.target for r in self._recent if r.proposed_at >= cutoff}

    def _record(self, target: str) -> None:
        self._recent.append(
            _ProposalRecord(target=target, proposed_at=datetime.now(timezone.utc))
        )
        if len(self._recent) > self.recent_history:
            self._recent = self._recent[-self.recent_history :]

    def propose_watchpoints(
        self,
        soul: dict,
        existing_wps: list[WatchPoint],
        *,
        max_new: int = 3,
    ) -> list[LlmProposal]:
        """LLM に問い合わせて提案リストを得る。 失敗時は空リスト。

        health.is_dead() の場合 (skip_when_dead=True なら) LLM を呼ばずに即返却。
        DEAD 状態からの復帰は probe_llm_health() を別途呼ぶ。
        """
        if max_new <= 0:
            return []
        if self.skip_when_dead and self.health.is_dead():
            logger.debug("LLM is DEAD, skipping proposal call (use probe to recover)")
            return []

        prompt = build_prompt(soul, existing_wps, max_new=max_new)
        try:
            response = self.llm_call(prompt)
            self.health.record_success()
        except Exception as exc:  # noqa: BLE001
            reason = f"{type(exc).__name__}: {exc}"
            self.health.record_failure(reason=reason)
            return []

        self.last_invocation_at = datetime.now(timezone.utc)
        self.last_response = response or ""

        existing_targets = {wp.target for wp in existing_wps}
        proposals = parse_proposals(
            self.last_response,
            existing_targets=existing_targets,
            recent_targets=self._recent_targets(),
        )
        # max_new で頭切り
        proposals = proposals[:max_new]
        for p in proposals:
            self._record(p.target)
        return proposals


# ════════════════════════════════════════════════════════════════════════════
# 健康プローブ (DEAD 状態からの自己治癒のため)
# ════════════════════════════════════════════════════════════════════════════


DEFAULT_PROBE_PROMPT = (
    "Reply with the single word OK and nothing else."
)


def probe_llm_health(
    proposer: "LlmWpProposer",
    *,
    probe_prompt: str = DEFAULT_PROBE_PROMPT,
) -> bool:
    """軽量 prompt で LLM の生死を確認し、 health を更新する。

    呼出側が定期的に (例: 1 時間毎の systemd timer から) 呼ぶことを想定。
    成功すれば proposer.health は HEALTHY に復帰し、 次の通常 evolve サイクル
    から再び LLM 提案が走るようになる。

    Returns:
        True if LLM responded; False otherwise (含む空応答)。
    """
    try:
        response = proposer.llm_call(probe_prompt)
    except Exception as exc:  # noqa: BLE001
        proposer.health.record_failure(
            reason=f"probe:{type(exc).__name__}"
        )
        return False
    if not isinstance(response, str) or not response.strip():
        proposer.health.record_failure(reason="probe:empty_response")
        return False
    proposer.health.record_success()
    return True


# ════════════════════════════════════════════════════════════════════════════
# 多段フォールバック (Bonsai が DEAD のとき Qwen 等に逃がす)
# ════════════════════════════════════════════════════════════════════════════


class ChainedLlmCall:
    """`primary` を優先しつつ、 失敗 / DEAD 時は `secondary` に逃がすコーラブル。

    使い方::

        chained = ChainedLlmCall(
            primary=bonsai_call,        # 1.7B, 高品質だが落ちやすい
            secondary=qwen05b_call,     # 0.5B, 提案品質は劣るが安定
        )
        proposer = LlmWpProposer(llm_call=chained)
        # → proposer.health は (chained 全体としての) 成功失敗を記録する
        # → chained.primary_health で primary 単独の健康度を別管理できる

    挙動:
        - primary が HEALTHY/DEGRADED: primary を試して成功すればそれ。
          失敗したらこの 1 コールだけ secondary に逃がす (= 即時 fallback)
        - primary が DEAD: primary を呼ばず、 直接 secondary を使う
        - probe_llm_health(primary_proposer) を別途定期実行することで
          primary が直ったら自動的に primary に戻る
    """

    def __init__(
        self,
        primary: Callable[[str], str],
        secondary: Callable[[str], str],
        *,
        primary_health: LlmHealthTracker | None = None,
    ):
        self.primary = primary
        self.secondary = secondary
        self.primary_health = primary_health or LlmHealthTracker()
        self.fallback_count: int = 0

    def __call__(self, prompt: str) -> str:
        # primary が DEAD → 即 secondary
        if self.primary_health.is_dead():
            self.fallback_count += 1
            return self.secondary(prompt)
        # primary を試す
        try:
            response = self.primary(prompt)
            self.primary_health.record_success()
            return response
        except Exception as exc:  # noqa: BLE001
            self.primary_health.record_failure(
                reason=f"primary:{type(exc).__name__}"
            )
            self.fallback_count += 1
            # この 1 コールだけ secondary に逃がす (再投げしない)
            return self.secondary(prompt)


# ════════════════════════════════════════════════════════════════════════════
# LLM プロセス再起動マネージャ (DEAD 永続化への対処)
# ════════════════════════════════════════════════════════════════════════════


@dataclass
class RestartAttempt:
    """1 回の restart 試行の記録"""

    started_at: datetime
    completed_at: datetime | None = None
    success: bool = False
    exit_code: int | None = None
    output: str = ""
    error: str = ""
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "started_at": self.started_at.isoformat(),
            "completed_at": (
                self.completed_at.isoformat() if self.completed_at else None
            ),
            "success": self.success,
            "exit_code": self.exit_code,
            "reason": self.reason,
            "output": self.output[:200],
            "error": self.error[:200],
        }


class LlmRestartManager:
    """LLM プロセスを外部コマンドで再起動するマネージャ。

    使い方::

        restarter = LlmRestartManager(
            restart_command=["sudo", "systemctl", "restart", "bonsai.service"],
            health=proposer.health,
            min_dead_minutes=5.0,        # DEAD が 5 分続いたら restart 検討
            max_restarts_per_hour=3,    # 1 時間に 3 回まで
            cooldown_minutes=10.0,       # restart 間の最小間隔
            dry_run=False,
        )

        # systemd timer から定期 (例: 5 分毎) に呼出
        attempt = restarter.maybe_restart()
        if attempt and attempt.success:
            # 直後に probe して health を更新 (呼出側の責務)
            probe_llm_health(proposer)

    安全装置:
        - DEAD 継続時間の最小値 (=即時に飛びつかない)
        - cooldown (=連続再起動の暴走防止)
        - rate limit (=直近 1h の上限)
        - subprocess.timeout (=コマンド自体のハング防止)
        - dry_run (=動作確認モード、 実コマンドを実行しない)
    """

    def __init__(
        self,
        restart_command: list[str],
        health: LlmHealthTracker,
        *,
        min_dead_minutes: float = 5.0,
        max_restarts_per_hour: int = 3,
        cooldown_minutes: float = 10.0,
        dry_run: bool = False,
        timeout_s: float = 60.0,
        history_limit: int = 100,
    ):
        if not restart_command:
            raise ValueError("restart_command must be non-empty")
        self.restart_command = list(restart_command)
        self.health = health
        self.min_dead_minutes = float(min_dead_minutes)
        self.max_restarts_per_hour = int(max_restarts_per_hour)
        self.cooldown_minutes = float(cooldown_minutes)
        self.dry_run = bool(dry_run)
        self.timeout_s = float(timeout_s)
        self.history_limit = int(history_limit)
        self.history: list[RestartAttempt] = []

    @staticmethod
    def _utcnow() -> datetime:
        return datetime.now(timezone.utc)

    # ── 判定 ──

    def should_restart(self) -> tuple[bool, str]:
        """restart すべきかと理由を返す"""
        if not self.health.is_dead():
            return False, "not dead"

        # DEAD 継続時間
        elapsed_min = (
            (self._utcnow() - self.health.state_changed_at).total_seconds() / 60
        )
        if elapsed_min < self.min_dead_minutes:
            return False, (
                f"dead only {elapsed_min:.1f}m < {self.min_dead_minutes:.1f}m"
            )

        # 直近 restart からの cooldown
        if self.history:
            last = self.history[-1]
            since_last_min = (
                (self._utcnow() - last.started_at).total_seconds() / 60
            )
            if since_last_min < self.cooldown_minutes:
                return False, (
                    f"cooldown ({since_last_min:.1f}m < {self.cooldown_minutes:.1f}m)"
                )

        # 直近 1 時間の rate limit
        cutoff = self._utcnow() - timedelta(hours=1)
        recent = [a for a in self.history if a.started_at >= cutoff]
        if len(recent) >= self.max_restarts_per_hour:
            return False, (
                f"rate limit ({len(recent)}/h ≥ {self.max_restarts_per_hour})"
            )

        return True, "ok"

    # ── 実行 ──

    def maybe_restart(self) -> RestartAttempt | None:
        """条件を満たしていれば restart 実行。 そうでなければ None。"""
        ok, reason = self.should_restart()
        if not ok:
            logger.debug(f"restart skipped: {reason}")
            return None
        return self.restart_now(reason=f"DEAD persists ({reason})")

    def restart_now(self, *, reason: str = "manual") -> RestartAttempt:
        """強制 restart。 dry_run なら実コマンドは実行しない。"""
        attempt = RestartAttempt(
            started_at=self._utcnow(),
            reason=reason,
        )
        cmd_str = " ".join(self.restart_command)

        if self.dry_run:
            attempt.completed_at = self._utcnow()
            attempt.success = True
            attempt.output = f"DRY RUN: would execute: {cmd_str}"
            logger.info(attempt.output)
            self._append_history(attempt)
            return attempt

        logger.warning(f"LLM restart triggered ({reason}): {cmd_str}")
        try:
            result = subprocess.run(
                self.restart_command,
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
            )
            attempt.exit_code = result.returncode
            attempt.output = (result.stdout or "")[:500]
            attempt.error = (result.stderr or "")[:500]
            attempt.success = result.returncode == 0
            attempt.completed_at = self._utcnow()
            if attempt.success:
                logger.info(
                    f"LLM restart succeeded: cmd={cmd_str} exit=0"
                )
            else:
                logger.warning(
                    f"LLM restart failed: cmd={cmd_str} "
                    f"exit={result.returncode} stderr={attempt.error[:160]}"
                )
        except subprocess.TimeoutExpired:
            attempt.completed_at = self._utcnow()
            attempt.error = f"restart command timed out after {self.timeout_s}s"
            attempt.success = False
            logger.error(attempt.error)
        except FileNotFoundError as exc:
            attempt.completed_at = self._utcnow()
            attempt.error = f"command not found: {exc}"
            attempt.success = False
            logger.error(attempt.error)
        except Exception as exc:  # noqa: BLE001
            attempt.completed_at = self._utcnow()
            attempt.error = f"{type(exc).__name__}: {exc}"
            attempt.success = False
            logger.error(f"LLM restart error: {attempt.error}")

        self._append_history(attempt)
        return attempt

    def _append_history(self, attempt: RestartAttempt) -> None:
        self.history.append(attempt)
        if len(self.history) > self.history_limit:
            self.history = self.history[-self.history_limit :]

    # ── 統計 ──

    def stats(self) -> dict:
        cutoff_24h = self._utcnow() - timedelta(hours=24)
        recent = [a for a in self.history if a.started_at >= cutoff_24h]
        successes = sum(1 for a in recent if a.success)
        last_success_at = next(
            (
                a.started_at.isoformat()
                for a in reversed(self.history)
                if a.success
            ),
            None,
        )
        return {
            "total_attempts": len(self.history),
            "attempts_24h": len(recent),
            "successes_24h": successes,
            "failures_24h": len(recent) - successes,
            "last_attempt_at": (
                self.history[-1].started_at.isoformat() if self.history else None
            ),
            "last_success_at": last_success_at,
            "dry_run": self.dry_run,
        }


# ════════════════════════════════════════════════════════════════════════════
# AdaptiveWatchPointPolicy: ルール + LLM のハイブリッド
# ════════════════════════════════════════════════════════════════════════════


class Policy(str, Enum):
    RULES_ONLY = "rules_only"
    LLM_ONLY = "llm_only"
    HYBRID = "hybrid"


@dataclass
class AdaptiveWatchPointPolicy:
    """既存 WatchPointRules と LlmWpProposer をまとめる。

    ハイブリッド時は:
      1. ルールが候補を返す
      2. 残り枠を LLM に求める
      3. すべての候補を WatchPointManager.propose() に渡す
    """

    rules: WatchPointRules
    proposer: LlmWpProposer | None = None
    policy: Policy = Policy.HYBRID
    max_new_per_cycle: int = 3
    min_episodes_since_last_call: int = 5

    def __post_init__(self) -> None:
        if isinstance(self.policy, str):
            self.policy = Policy(self.policy)
        if self.policy in (Policy.LLM_ONLY, Policy.HYBRID) and self.proposer is None:
            # LLM なしで HYBRID/LLM_ONLY は無理 → 安全側で RULES_ONLY に降格
            self.policy = Policy.RULES_ONLY

    def gather_proposals(
        self,
        *,
        soul: dict,
        existing_wps: list[WatchPoint],
        topic_history: list[dict] | None = None,
        recent_episodes: list[dict] | None = None,
        previous_identity: dict | None = None,
    ) -> list[dict]:
        """このサイクルで採用すべき WP 提案 (propose() 引数 dict のリスト) を返す。

        Returns:
            list of dicts compatible with WatchPointManager.propose(**kwargs).
        """
        existing_targets = {wp.target for wp in existing_wps}
        results: list[dict] = []

        # 1. ルール経路 (RULES_ONLY / HYBRID)
        if self.policy in (Policy.RULES_ONLY, Policy.HYBRID):
            rule_candidates = self._gather_rule_candidates(
                soul=soul,
                topic_history=topic_history,
                recent_episodes=recent_episodes,
                previous_identity=previous_identity,
            )
            for cand in rule_candidates:
                if cand["target"] in existing_targets:
                    continue
                if any(r["target"] == cand["target"] for r in results):
                    continue
                results.append(cand)
                if len(results) >= self.max_new_per_cycle:
                    return results

        # 2. LLM 経路 (LLM_ONLY / HYBRID)
        if self.policy in (Policy.LLM_ONLY, Policy.HYBRID):
            slots = self.max_new_per_cycle - len(results)
            if slots > 0 and self.proposer is not None:
                # 既存 + ルール提案分を併せて重複除外
                merged_existing = list(existing_wps)
                # 仮想 WP として表現は不要、 target だけ見るので直接 set 拡張
                proposals = self.proposer.propose_watchpoints(
                    soul=soul,
                    existing_wps=merged_existing,
                    max_new=slots,
                )
                already = existing_targets | {r["target"] for r in results}
                for p in proposals:
                    if p.target in already:
                        continue
                    results.append(p.to_propose_kwargs())
                    already.add(p.target)
                    if len(results) >= self.max_new_per_cycle:
                        break

        return results

    # ── 内部 ──

    def _gather_rule_candidates(
        self,
        *,
        soul: dict,
        topic_history: list[dict] | None,
        recent_episodes: list[dict] | None,
        previous_identity: dict | None,
    ) -> list[dict]:
        """既存 WatchPointRules.check_*() を順に呼ぶ。"""
        candidates: list[dict] = []
        if topic_history:
            for c in self.rules.check_topic_concentration(topic_history):
                candidates.append(self._normalize_rule_candidate(c))
        if recent_episodes:
            for c in self.rules.check_importance_spike(recent_episodes):
                candidates.append(self._normalize_rule_candidate(c))
        if previous_identity:
            for c in self.rules.check_personality_uncertainty(
                soul.get("core_identity", {}),
                previous_identity,
            ):
                candidates.append(self._normalize_rule_candidate(c))
        return candidates

    @staticmethod
    def _normalize_rule_candidate(cand: dict) -> dict:
        # WatchPointRules は dict を返す。 propose() 互換に補正
        out = dict(cand)
        # target は normalize しておく
        if isinstance(out.get("target"), str):
            out["target"] = _normalize_target(out["target"])
        # affects_dimensions のフィルタ
        affects = out.get("affects_dimensions") or []
        out["affects_dimensions"] = [
            d for d in affects if isinstance(d, str) and d in KNOWN_DIMENSIONS
        ]
        return out
