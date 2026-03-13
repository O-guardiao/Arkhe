"""
RLM Skill Telemetry

Camada leve de telemetria para skills e roteamento.

Objetivos:
- registrar decisões de roteamento por query;
- registrar execução de callables SIF por skill;
- expor estatísticas agregadas para ranking híbrido;
- persistir traces em JSONL sem introduzir dependência externa.
"""
from __future__ import annotations

import contextvars
import json
import os
import re
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from rlm.core.semantic_retrieval import SemanticTextIndex, semantic_similarity
from rlm.core.structured_log import get_logger

telemetry_log = get_logger("skill_telemetry")

_current_session_id: contextvars.ContextVar[str] = contextvars.ContextVar("skill_session_id", default="")
_current_client_id: contextvars.ContextVar[str] = contextvars.ContextVar("skill_client_id", default="")
_current_query: contextvars.ContextVar[str] = contextvars.ContextVar("skill_query", default="")


@dataclass
class SkillTraceEvent:
    event_type: str
    timestamp: float
    skill_name: str = ""
    session_id: str = ""
    client_id: str = ""
    query: str = ""
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class SkillAggregateStats:
    route_count: int = 0
    call_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    total_latency_ms: float = 0.0
    utility_hits: int = 0

    @property
    def success_rate(self) -> float:
        total = self.success_count + self.failure_count
        if total <= 0:
            return 0.5
        return self.success_count / total

    @property
    def avg_latency_ms(self) -> float:
        if self.call_count <= 0:
            return 0.0
        return self.total_latency_ms / self.call_count


class SkillTelemetryStore:
    def __init__(
        self,
        max_events: int = 2000,
        trace_path: str | Path | None = None,
        *,
        load_existing: bool = True,
    ):
        self._events: deque[SkillTraceEvent] = deque(maxlen=max_events)
        self._stats: dict[str, SkillAggregateStats] = {}
        self._transitions: dict[str, dict[str, int]] = {}
        self._session_transitions: dict[str, dict[str, dict[str, int]]] = {}
        self._last_skill_by_session: dict[str, str] = {}
        self._lock = threading.Lock()
        if trace_path is None:
            trace_path = os.environ.get("RLM_SKILL_TRACE_FILE", ".rlm_workspace/skill_traces.jsonl")
        self._trace_path = Path(trace_path)
        if load_existing:
            self._rehydrate_from_disk()

    def set_context(self, session_id: str = "", client_id: str = "", query: str = "") -> dict[str, contextvars.Token]:
        return {
            "session_id": _current_session_id.set(session_id),
            "client_id": _current_client_id.set(client_id),
            "query": _current_query.set(query),
        }

    def reset_context(self, tokens: dict[str, contextvars.Token] | None) -> None:
        if not tokens:
            return
        _current_session_id.reset(tokens["session_id"])
        _current_client_id.reset(tokens["client_id"])
        _current_query.reset(tokens["query"])

    def current_context(self) -> dict[str, str]:
        return {
            "session_id": _current_session_id.get(),
            "client_id": _current_client_id.get(),
            "query": _current_query.get(),
        }

    def record_routing(
        self,
        *,
        mode: str,
        query: str,
        ranked_skills: list[dict[str, Any]],
        selected_skills: list[str],
        blocked_skills: list[dict[str, Any]] | None = None,
        session_id: str = "",
        client_id: str = "",
    ) -> None:
        session_id = session_id or _current_session_id.get()
        client_id = client_id or _current_client_id.get()
        event = SkillTraceEvent(
            event_type="routing",
            timestamp=time.time(),
            session_id=session_id,
            client_id=client_id,
            query=query,
            payload={
                "mode": mode,
                "ranked_skills": ranked_skills[:8],
                "selected_skills": selected_skills,
                "blocked_skills": (blocked_skills or [])[:8],
            },
        )
        self._ingest_event(event, persist=True)

    def record_call(
        self,
        *,
        skill_name: str,
        success: bool,
        latency_ms: float,
        args_preview: str = "",
        error: str = "",
        utility_hit: bool = True,
        session_id: str = "",
        client_id: str = "",
        query: str = "",
    ) -> None:
        session_id = session_id or _current_session_id.get()
        client_id = client_id or _current_client_id.get()
        query = query or _current_query.get()
        event = SkillTraceEvent(
            event_type="call",
            timestamp=time.time(),
            skill_name=skill_name,
            session_id=session_id,
            client_id=client_id,
            query=query,
            payload={
                "success": success,
                "latency_ms": round(latency_ms, 3),
                "args_preview": args_preview[:160],
                "error": error[:200],
                "utility_hit": utility_hit,
            },
        )
        self._ingest_event(event, persist=True)

    def record_handoff(
        self,
        *,
        payload: dict[str, Any],
        session_id: str = "",
        client_id: str = "",
        query: str = "",
    ) -> None:
        session_id = session_id or _current_session_id.get()
        client_id = client_id or _current_client_id.get()
        query = query or _current_query.get()
        event = SkillTraceEvent(
            event_type="handoff",
            timestamp=time.time(),
            session_id=session_id,
            client_id=client_id,
            query=query,
            payload=dict(payload),
        )
        self._ingest_event(event, persist=True)

    def get_skill_stats(self, skill_name: str) -> dict[str, Any]:
        with self._lock:
            stats = self._stats.get(skill_name, SkillAggregateStats())
            return {
                "route_count": stats.route_count,
                "call_count": stats.call_count,
                "success_count": stats.success_count,
                "failure_count": stats.failure_count,
                "success_rate": round(stats.success_rate, 4),
                "avg_latency_ms": round(stats.avg_latency_ms, 3),
                "utility_hits": stats.utility_hits,
            }

    def get_skill_success_rate(self, skill_name: str) -> float:
        with self._lock:
            stats = self._stats.get(skill_name)
            return stats.success_rate if stats is not None else 0.5

    def get_skill_utility_rate(self, skill_name: str) -> float:
        with self._lock:
            stats = self._stats.get(skill_name)
            if stats is None or stats.call_count <= 0:
                return 0.5
            return round(stats.utility_hits / max(stats.call_count, 1), 4)

    def get_recent_events(
        self,
        limit: int = 50,
        *,
        event_type: str = "",
        skill_name: str = "",
        session_id: str = "",
    ) -> list[dict[str, Any]]:
        with self._lock:
            events = [
                event
                for event in self._events
                if self._matches(
                    event,
                    event_type=event_type,
                    skill_name=skill_name,
                    session_id=session_id,
                )
            ][-limit:]
        return [asdict(event) for event in events]

    def get_summary(self, *, include_recent: bool = False, limit: int = 20) -> dict[str, Any]:
        with self._lock:
            stats = {
                skill_name: {
                    "route_count": entry.route_count,
                    "call_count": entry.call_count,
                    "success_rate": round(entry.success_rate, 4),
                    "avg_latency_ms": round(entry.avg_latency_ms, 3),
                    "utility_hits": entry.utility_hits,
                }
                for skill_name, entry in self._stats.items()
            }
            events = list(self._events)
            summary = {
                "tracked_skills": len(stats),
                "events_buffered": len(events),
                "route_events": sum(1 for event in events if event.event_type == "routing"),
                "call_events": sum(1 for event in events if event.event_type == "call"),
                "handoff_events": sum(1 for event in events if event.event_type == "handoff"),
                "transition_edges": sum(len(targets) for targets in self._transitions.values()),
                "sessions_with_transitions": len(self._session_transitions),
                "trace_file": str(self._trace_path),
                "skills": stats,
                "transitions": {
                    source: dict(sorted(targets.items(), key=lambda item: (-item[1], item[0])))
                    for source, targets in sorted(self._transitions.items())
                },
            }
            if include_recent:
                summary["recent_events"] = [asdict(event) for event in events[-limit:]]
            return summary

    def get_skill_report(self, skill_name: str, *, limit: int = 20) -> dict[str, Any]:
        return {
            "skill": skill_name,
            "stats": self.get_skill_stats(skill_name),
            "recent_events": self.get_recent_events(limit=limit, skill_name=skill_name),
            "transitions": self.get_transition_targets(skill_name),
        }

    def get_transition_score(self, source_skill: str, target_skill: str) -> int:
        with self._lock:
            return self._transitions.get(source_skill, {}).get(target_skill, 0)

    def get_transition_targets(self, source_skill: str) -> dict[str, int]:
        with self._lock:
            targets = self._transitions.get(source_skill, {})
            return dict(sorted(targets.items(), key=lambda item: (-item[1], item[0])))

    def get_transition_insights(self, source_skill: str, target_skill: str) -> dict[str, Any]:
        count = self.get_transition_score(source_skill, target_skill)
        stats = self.get_skill_stats(target_skill)
        utility_rate = self.get_skill_utility_rate(target_skill)
        avg_latency_ms = float(stats.get("avg_latency_ms", 0.0))
        weighted_score = (
            float(count)
            + float(stats.get("success_rate", 0.5)) * 1.5
            + utility_rate
            - min(avg_latency_ms / 1000.0, 2.0) * 0.2
        )
        return {
            "source": source_skill,
            "target": target_skill,
            "count": count,
            "success_rate": round(float(stats.get("success_rate", 0.5)), 4),
            "utility_rate": utility_rate,
            "avg_latency_ms": round(avg_latency_ms, 3),
            "weighted_score": round(weighted_score, 4),
        }

    def get_weighted_transition_targets(self, source_skill: str) -> dict[str, dict[str, Any]]:
        targets = self.get_transition_targets(source_skill)
        insights = {
            target: self.get_transition_insights(source_skill, target)
            for target in targets
        }
        ordered = sorted(
            insights.items(),
            key=lambda item: (-float(item[1]["weighted_score"]), -int(item[1]["count"]), item[0]),
        )
        return {target: payload for target, payload in ordered}

    def get_session_transition_targets(self, session_id: str, source_skill: str = "") -> dict[str, Any]:
        with self._lock:
            session_edges = self._session_transitions.get(session_id, {})
            if source_skill:
                targets = session_edges.get(source_skill, {})
                return dict(sorted(targets.items(), key=lambda item: (-item[1], item[0])))
            return {
                source: dict(sorted(targets.items(), key=lambda item: (-item[1], item[0])))
                for source, targets in sorted(session_edges.items())
            }

    def get_transition_report(self, *, session_id: str = "", limit: int = 10) -> dict[str, Any]:
        if session_id:
            transitions = self.get_session_transition_targets(session_id)
            flat_edges = [
                {"source": source, "target": target, "count": count}
                for source, targets in transitions.items()
                for target, count in targets.items()
            ]
            flat_edges.sort(key=lambda item: (-item["count"], item["source"], item["target"]))
            return {
                "session_id": session_id,
                "transition_edges": len(flat_edges),
                "transitions": transitions,
                "top_edges": flat_edges[:limit],
            }

        with self._lock:
            flat_edges = [
                {"source": source, "target": target, "count": count}
                for source, targets in self._transitions.items()
                for target, count in targets.items()
            ]
        flat_edges.sort(key=lambda item: (-item["count"], item["source"], item["target"]))
        return {
            "transition_edges": len(flat_edges),
            "top_edges": flat_edges[:limit],
            "weighted_transitions": {
                source: self.get_weighted_transition_targets(source)
                for source in sorted(self._transitions)
            },
        }

    def get_relevant_traces(
        self,
        query: str,
        *,
        skill_name: str = "",
        event_type: str = "",
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        query_terms = self._tokenize(query)
        if not query_terms and not query.strip():
            return []

        with self._lock:
            candidates: list[tuple[str, SkillTraceEvent, str]] = []
            for event in self._events:
                if event_type and event.event_type != event_type:
                    continue
                if skill_name and event.skill_name != skill_name:
                    continue
                search_text = self._event_search_text(event)
                candidates.append((f"{event.timestamp}:{len(candidates)}", event, search_text))

        index = SemanticTextIndex((key, text) for key, _event, text in candidates)
        semantic_hits = {
            str(item["key"]): float(item["similarity"])
            for item in index.search(query, top_k=max(limit * 4, 12))
        }

        ranked: list[tuple[float, float, float, SkillTraceEvent]] = []
        for key, event, search_text in candidates:
            haystack_terms = self._tokenize(search_text)
            lexical_overlap = len(query_terms & haystack_terms)
            lexical_score = lexical_overlap / max(len(query_terms), 1) if query_terms else 0.0
            semantic_score = semantic_hits.get(key, semantic_similarity(query, search_text))
            if lexical_score <= 0.0 and semantic_score < 0.12:
                continue
            score = max(lexical_score, semantic_score)
            if event.event_type == "call" and event.payload.get("success"):
                score += 0.15
            ranked.append((score, semantic_score, lexical_score, event))

        ranked.sort(key=lambda item: (-item[0], -item[1], -item[3].timestamp))
        results: list[dict[str, Any]] = []
        for score, semantic_score, lexical_score, event in ranked[:limit]:
            payload = asdict(event)
            payload["retrieval_score"] = round(score, 4)
            payload["semantic_score"] = round(semantic_score, 4)
            payload["lexical_score"] = round(lexical_score, 4)
            results.append(payload)
        return results

    def get_trace_relevance_score(self, query: str, skill_name: str, *, limit: int = 3) -> float:
        traces = self.get_relevant_traces(query, skill_name=skill_name, limit=limit)
        if not traces:
            return 0.0
        score = sum(float(trace["retrieval_score"]) for trace in traces) / len(traces)
        return round(score, 4)

    def _matches(
        self,
        event: SkillTraceEvent,
        *,
        event_type: str = "",
        skill_name: str = "",
        session_id: str = "",
    ) -> bool:
        if event_type and event.event_type != event_type:
            return False
        if skill_name and event.skill_name != skill_name:
            return False
        if session_id and event.session_id != session_id:
            return False
        return True

    def _event_search_text(self, event: SkillTraceEvent) -> str:
        parts = [event.query, event.skill_name]
        args_preview = event.payload.get("args_preview")
        if isinstance(args_preview, str):
            parts.append(args_preview)
        selected_skills = event.payload.get("selected_skills")
        if isinstance(selected_skills, list):
            parts.extend(str(item) for item in selected_skills)
        attempted_skills = event.payload.get("attempted_skills")
        if isinstance(attempted_skills, list):
            parts.extend(str(item) for item in attempted_skills)
        failures = event.payload.get("failures")
        if isinstance(failures, list):
            parts.extend(str(item) for item in failures)
        for key in ("target_role", "reason", "remaining_goal", "summary"):
            value = event.payload.get(key)
            if isinstance(value, str):
                parts.append(value)
        return " ".join(part for part in parts if part)

    def _tokenize(self, text: str) -> set[str]:
        return {term for term in re.findall(r"[a-zA-Z0-9_]+", text.lower()) if len(term) > 1}

    def reset(self) -> None:
        with self._lock:
            self._events.clear()
            self._stats.clear()
            self._transitions.clear()
            self._session_transitions.clear()
            self._last_skill_by_session.clear()

    def reload_from_disk(self) -> None:
        self.reset()
        self._rehydrate_from_disk()

    def _ingest_event(self, event: SkillTraceEvent, *, persist: bool) -> None:
        with self._lock:
            self._events.append(event)
            if event.event_type == "routing":
                for skill_name in event.payload.get("selected_skills", []):
                    self._stats.setdefault(skill_name, SkillAggregateStats()).route_count += 1
            elif event.event_type == "call":
                stats = self._stats.setdefault(event.skill_name, SkillAggregateStats())
                stats.call_count += 1
                stats.total_latency_ms += float(event.payload.get("latency_ms", 0.0))
                if event.payload.get("success"):
                    stats.success_count += 1
                else:
                    stats.failure_count += 1
                if event.payload.get("utility_hit", True):
                    stats.utility_hits += 1
                if event.session_id:
                    previous_skill = self._last_skill_by_session.get(event.session_id)
                    if previous_skill and previous_skill != event.skill_name:
                        edges = self._transitions.setdefault(previous_skill, {})
                        edges[event.skill_name] = edges.get(event.skill_name, 0) + 1
                        session_edges = self._session_transitions.setdefault(event.session_id, {})
                        session_targets = session_edges.setdefault(previous_skill, {})
                        session_targets[event.skill_name] = session_targets.get(event.skill_name, 0) + 1
                    self._last_skill_by_session[event.session_id] = event.skill_name
        if persist:
            self._persist(event)

    def _rehydrate_from_disk(self) -> None:
        if not self._trace_path.exists():
            return
        try:
            with self._trace_path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    raw = line.strip()
                    if not raw:
                        continue
                    try:
                        payload = json.loads(raw)
                        event = SkillTraceEvent(
                            event_type=str(payload.get("event_type", "")),
                            timestamp=float(payload.get("timestamp", 0.0)),
                            skill_name=str(payload.get("skill_name", "")),
                            session_id=str(payload.get("session_id", "")),
                            client_id=str(payload.get("client_id", "")),
                            query=str(payload.get("query", "")),
                            payload=dict(payload.get("payload", {}) or {}),
                        )
                        if not event.event_type:
                            continue
                        self._ingest_event(event, persist=False)
                    except Exception as exc:
                        telemetry_log.warn(f"Skill telemetry replay skipped invalid line: {exc}")
        except Exception as exc:
            telemetry_log.warn(f"Skill telemetry replay failed: {exc}")

    def _persist(self, event: SkillTraceEvent) -> None:
        try:
            self._trace_path.parent.mkdir(parents=True, exist_ok=True)
            with self._trace_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(asdict(event), ensure_ascii=False) + "\n")
        except Exception as exc:
            telemetry_log.warn(f"Skill telemetry persist failed: {exc}")


_STORE = SkillTelemetryStore()


def get_skill_telemetry() -> SkillTelemetryStore:
    return _STORE
