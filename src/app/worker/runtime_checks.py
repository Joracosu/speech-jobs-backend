"""Shared worker runtime checks for ASR, diarization, and CLI preflight."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import Literal


SUPPORTED_DEVICE_PREFERENCES = {"auto", "cpu", "cuda"}
ISSUE_DEPENDENCY_MISSING = "dependency_missing"
ISSUE_CONFIG_MISSING = "config_missing"
ISSUE_CUDA_UNAVAILABLE = "cuda_unavailable"
ISSUE_RUNTIME_ERROR = "runtime_error"
IssueKind = Literal[
    "dependency_missing",
    "config_missing",
    "cuda_unavailable",
    "runtime_error",
]


@dataclass(slots=True, frozen=True)
class RuntimeIssue:
    """Minimal internal issue shape used by runtime checks and adapters."""

    component: str
    kind: IssueKind
    message: str


@dataclass(slots=True, frozen=True)
class ComponentRuntimeStatus:
    """Readiness report for one worker runtime component."""

    component: str
    requested_device: str
    resolved_device: str | None
    ready: bool
    issues: tuple[RuntimeIssue, ...]


@dataclass(slots=True, frozen=True)
class WorkerRuntimeStatus:
    """Aggregated readiness report for the worker runtime."""

    requested_device: str
    ready: bool
    asr: ComponentRuntimeStatus
    diarization: ComponentRuntimeStatus

    @property
    def components(self) -> tuple[ComponentRuntimeStatus, ComponentRuntimeStatus]:
        """Return the component reports in display order."""
        return (self.asr, self.diarization)


def _runtime_issue(component: str, kind: IssueKind, message: str) -> RuntimeIssue:
    """Build one internal runtime issue."""
    return RuntimeIssue(component=component, kind=kind, message=message)


def _ready_status(
    component: str,
    requested_device: str,
    resolved_device: str,
) -> ComponentRuntimeStatus:
    """Build a successful readiness report."""
    return ComponentRuntimeStatus(
        component=component,
        requested_device=requested_device,
        resolved_device=resolved_device,
        ready=True,
        issues=(),
    )


def _not_ready_status(
    component: str,
    requested_device: str,
    resolved_device: str | None,
    issue: RuntimeIssue,
) -> ComponentRuntimeStatus:
    """Build a failed readiness report."""
    return ComponentRuntimeStatus(
        component=component,
        requested_device=requested_device,
        resolved_device=resolved_device,
        ready=False,
        issues=(issue,),
    )


def _normalize_requested_device(component: str, requested_device: str) -> str:
    """Validate and normalize the requested device preference."""
    if requested_device in SUPPORTED_DEVICE_PREFERENCES:
        return requested_device

    raise ValueError(
        f"Unsupported {component} device preference '{requested_device}'."
    )


def _import_ctranslate2() -> object:
    """Import ctranslate2 for ASR runtime checks."""
    return import_module("ctranslate2")


def _import_faster_whisper() -> object:
    """Import faster-whisper for ASR runtime checks."""
    return import_module("faster_whisper")


def _import_torch() -> object:
    """Import torch for diarization runtime checks."""
    return import_module("torch")


def _import_pyannote_audio() -> object:
    """Import pyannote.audio for diarization runtime checks."""
    return import_module("pyannote.audio")


def _get_asr_cuda_device_count(ctranslate2_module: object) -> int:
    """Return the number of CUDA devices visible to ctranslate2."""
    return int(ctranslate2_module.get_cuda_device_count())


def _get_torch_cuda_state(torch_module: object) -> tuple[bool, int]:
    """Return CUDA availability and device count from torch."""
    cuda_available = bool(torch_module.cuda.is_available())
    device_count = int(torch_module.cuda.device_count())
    return cuda_available, device_count


def get_primary_issue(
    status: ComponentRuntimeStatus,
) -> RuntimeIssue | None:
    """Return the first issue for a component readiness report."""
    return status.issues[0] if status.issues else None


def inspect_asr_runtime(requested_device: str) -> ComponentRuntimeStatus:
    """Inspect whether the ASR runtime is ready for the requested device."""
    component = "asr"
    try:
        normalized_device = _normalize_requested_device(component, requested_device)
    except ValueError as exc:
        return _not_ready_status(
            component=component,
            requested_device=requested_device,
            resolved_device=None,
            issue=_runtime_issue(
                component=component,
                kind=ISSUE_RUNTIME_ERROR,
                message=str(exc),
            ),
        )

    try:
        ctranslate2_module = _import_ctranslate2()
    except Exception as exc:
        return _not_ready_status(
            component=component,
            requested_device=normalized_device,
            resolved_device=None if normalized_device == "auto" else normalized_device,
            issue=_runtime_issue(
                component=component,
                kind=ISSUE_DEPENDENCY_MISSING,
                message=f"ASR dependency 'ctranslate2' is unavailable: {exc}",
            ),
        )

    try:
        _import_faster_whisper()
    except Exception as exc:
        return _not_ready_status(
            component=component,
            requested_device=normalized_device,
            resolved_device=None if normalized_device == "auto" else normalized_device,
            issue=_runtime_issue(
                component=component,
                kind=ISSUE_DEPENDENCY_MISSING,
                message=f"ASR dependency 'faster-whisper' is unavailable: {exc}",
            ),
        )

    if normalized_device == "cpu":
        return _ready_status(component=component, requested_device=normalized_device, resolved_device="cpu")

    try:
        cuda_device_count = _get_asr_cuda_device_count(ctranslate2_module)
    except Exception as exc:
        return _not_ready_status(
            component=component,
            requested_device=normalized_device,
            resolved_device=None if normalized_device == "auto" else normalized_device,
            issue=_runtime_issue(
                component=component,
                kind=ISSUE_RUNTIME_ERROR,
                message=f"Unable to inspect ASR CUDA runtime: {exc}",
            ),
        )

    if normalized_device == "cuda":
        if cuda_device_count <= 0:
            return _not_ready_status(
                component=component,
                requested_device=normalized_device,
                resolved_device="cuda",
                issue=_runtime_issue(
                    component=component,
                    kind=ISSUE_CUDA_UNAVAILABLE,
                    message="CUDA was requested for ASR, but ctranslate2 does not see any CUDA devices.",
                ),
            )
        return _ready_status(
            component=component,
            requested_device=normalized_device,
            resolved_device="cuda",
        )

    resolved_device = "cuda" if cuda_device_count > 0 else "cpu"
    return _ready_status(
        component=component,
        requested_device=normalized_device,
        resolved_device=resolved_device,
    )


def inspect_diarization_runtime(
    requested_device: str,
    *,
    model_id: str,
    huggingface_token: str | None,
) -> ComponentRuntimeStatus:
    """Inspect whether the diarization runtime is ready for the requested device."""
    component = "diarization"
    try:
        normalized_device = _normalize_requested_device(component, requested_device)
    except ValueError as exc:
        return _not_ready_status(
            component=component,
            requested_device=requested_device,
            resolved_device=None,
            issue=_runtime_issue(
                component=component,
                kind=ISSUE_RUNTIME_ERROR,
                message=str(exc),
            ),
        )

    try:
        torch_module = _import_torch()
    except Exception as exc:
        return _not_ready_status(
            component=component,
            requested_device=normalized_device,
            resolved_device=None if normalized_device == "auto" else normalized_device,
            issue=_runtime_issue(
                component=component,
                kind=ISSUE_DEPENDENCY_MISSING,
                message=f"Diarization dependency 'torch' is unavailable: {exc}",
            ),
        )

    try:
        _import_pyannote_audio()
    except Exception as exc:
        return _not_ready_status(
            component=component,
            requested_device=normalized_device,
            resolved_device=None if normalized_device == "auto" else normalized_device,
            issue=_runtime_issue(
                component=component,
                kind=ISSUE_DEPENDENCY_MISSING,
                message=f"Diarization dependency 'pyannote.audio' is unavailable: {exc}",
            ),
        )

    if not model_id or not model_id.strip():
        return _not_ready_status(
            component=component,
            requested_device=normalized_device,
            resolved_device=None if normalized_device == "auto" else normalized_device,
            issue=_runtime_issue(
                component=component,
                kind=ISSUE_CONFIG_MISSING,
                message="DIARIZATION_MODEL_ID is required for diarization runtime.",
            ),
        )

    if not huggingface_token:
        return _not_ready_status(
            component=component,
            requested_device=normalized_device,
            resolved_device=None if normalized_device == "auto" else normalized_device,
            issue=_runtime_issue(
                component=component,
                kind=ISSUE_CONFIG_MISSING,
                message="HUGGINGFACE_TOKEN is required for diarization runtime.",
            ),
        )

    if normalized_device == "cpu":
        return _ready_status(
            component=component,
            requested_device=normalized_device,
            resolved_device="cpu",
        )

    try:
        cuda_available, cuda_device_count = _get_torch_cuda_state(torch_module)
    except Exception as exc:
        return _not_ready_status(
            component=component,
            requested_device=normalized_device,
            resolved_device=None if normalized_device == "auto" else normalized_device,
            issue=_runtime_issue(
                component=component,
                kind=ISSUE_RUNTIME_ERROR,
                message=f"Unable to inspect diarization CUDA runtime: {exc}",
            ),
        )

    if normalized_device == "cuda":
        if not cuda_available or cuda_device_count <= 0:
            return _not_ready_status(
                component=component,
                requested_device=normalized_device,
                resolved_device="cuda",
                issue=_runtime_issue(
                    component=component,
                    kind=ISSUE_CUDA_UNAVAILABLE,
                    message="CUDA was requested for diarization, but torch does not report any CUDA devices.",
                ),
            )
        return _ready_status(
            component=component,
            requested_device=normalized_device,
            resolved_device="cuda",
        )

    resolved_device = "cuda" if cuda_available and cuda_device_count > 0 else "cpu"
    return _ready_status(
        component=component,
        requested_device=normalized_device,
        resolved_device=resolved_device,
    )


def inspect_worker_runtime(
    requested_device: str,
    *,
    model_id: str,
    huggingface_token: str | None,
) -> WorkerRuntimeStatus:
    """Inspect the complete worker runtime for the requested device path."""
    asr_status = inspect_asr_runtime(requested_device)
    diarization_status = inspect_diarization_runtime(
        requested_device,
        model_id=model_id,
        huggingface_token=huggingface_token,
    )
    return WorkerRuntimeStatus(
        requested_device=requested_device,
        ready=asr_status.ready and diarization_status.ready,
        asr=asr_status,
        diarization=diarization_status,
    )


def format_worker_runtime_report(report: WorkerRuntimeStatus) -> str:
    """Return a compact human-readable preflight report."""
    lines = [
        f"Worker runtime preflight: {'READY' if report.ready else 'NOT READY'}",
        f"Requested device: {report.requested_device}",
    ]

    for status in report.components:
        resolved_device = status.resolved_device or "n/a"
        component_label = status.component.upper()
        lines.append(
            f"- {component_label}: {'READY' if status.ready else 'NOT READY'} "
            f"(requested={status.requested_device}, resolved={resolved_device})"
        )
        for issue in status.issues:
            lines.append(f"  * [{issue.kind}] {issue.message}")

    return "\n".join(lines)
