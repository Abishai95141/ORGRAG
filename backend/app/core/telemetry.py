"""Tracing with OpenTelemetry, exported over OTLP/HTTP (roadmap §3.6).

Off unless an exporter is configured: `LANGFUSE_PUBLIC_KEY` + `LANGFUSE_SECRET_KEY`
(+ `LANGFUSE_HOST`) send traces to Langfuse's OTLP endpoint; otherwise
`OTEL_EXPORTER_OTLP_ENDPOINT` (+ `OTEL_EXPORTER_OTLP_HEADERS`) sends them to any
collector. Unconfigured, the API's tracer is a no-op and spans cost next to nothing.

What a chat answer looks like: one trace per answer (`chat`, the Langfuse session is
the conversation) with a span per model turn (model, token usage), per tool call and
per retrieval stage. Every span carries `strata.org_id`, so latency and spend can be
split per org. Question, answer and tool-argument text is only attached with
`TRACE_CONTENT=true`, since traces leave the deployment and documents are private.
"""

import asyncio
import base64
import contextlib
import json
import logging
from collections.abc import Iterator
from typing import Any

from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.trace import Span, Status, StatusCode

from app.core.config import get_settings

log = logging.getLogger("strata.telemetry")
tracer = trace.get_tracer("strata")
_provider = None


def exporter_config() -> tuple[str, dict[str, str]] | None:
    """(traces endpoint, headers) from settings, or None when tracing is off."""
    s = get_settings()
    if s.langfuse_public_key and s.langfuse_secret_key:
        token = base64.b64encode(
            f"{s.langfuse_public_key}:{s.langfuse_secret_key}".encode()
        ).decode()
        return (
            f"{s.langfuse_host.rstrip('/')}/api/public/otel/v1/traces",
            {"Authorization": f"Basic {token}", "x-langfuse-ingestion-version": "4"},
        )
    if s.otel_exporter_otlp_endpoint:
        headers = {}
        for pair in (s.otel_exporter_otlp_headers or "").split(","):
            if "=" in pair:
                k, v = pair.split("=", 1)
                headers[k.strip()] = v.strip()
        return f"{s.otel_exporter_otlp_endpoint.rstrip('/')}/v1/traces", headers
    return None


def setup(service: str, exporter=None) -> bool:
    """Install the tracer provider once per process. `exporter` overrides the OTLP
    exporter (tests). Returns whether tracing is on."""
    global _provider
    if _provider is not None:
        return True
    config = exporter_config()
    if exporter is None and config is None:
        return False
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor

    provider = TracerProvider(
        resource=Resource.create({"service.name": service, "service.namespace": "strata"})
    )
    if exporter is not None:
        provider.add_span_processor(SimpleSpanProcessor(exporter))
    else:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        endpoint, headers = config
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, headers=headers))
        )
        log.info("Tracing to %s", endpoint)
    trace.set_tracer_provider(provider)
    _provider = provider
    return True


def shutdown() -> None:
    """Flush buffered spans (on process exit)."""
    global _provider
    if _provider is not None:
        _provider.shutdown()
        _provider = None


def capture_content() -> bool:
    return get_settings().trace_content


def _clean(attrs: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for k, v in attrs.items():
        if v is None:
            continue
        if isinstance(v, dict):
            v = json.dumps(v, default=str)
        elif not isinstance(v, (str, bool, int, float, list, tuple)):
            v = str(v)
        out[k] = v
    return out


@contextlib.contextmanager
def span(name: str, kind: str = "span", *, root: bool = False, **attrs: Any) -> Iterator[Span]:
    """A span with Langfuse's observation type (`span`, `generation`, `tool`,
    `retriever`, `agent`). `root` starts a new trace instead of nesting under the
    current one (a chat answer outlives the HTTP request that started it). Errors
    mark the span and propagate."""
    ctx = otel_context.Context() if root else None
    with tracer.start_as_current_span(
        name,
        context=ctx,
        attributes=_clean({"langfuse.observation.type": kind, **attrs}),
        record_exception=False,
        set_status_on_exception=False,
    ) as current:
        try:
            yield current
        except BaseException as exc:
            fail(current, exc)
            raise


def fail(target: Span, exc: BaseException | str) -> None:
    """Mark a span failed. Cancellation is a user stopping the answer, not a failure."""
    if isinstance(exc, (asyncio.CancelledError, GeneratorExit)):
        return
    target.set_status(Status(StatusCode.ERROR, exc if isinstance(exc, str) else type(exc).__name__))
    if isinstance(exc, BaseException):
        target.record_exception(exc)


def set_attrs(target: Span, **attrs: Any) -> None:
    if target.is_recording():
        target.set_attributes(_clean(attrs))


def org_attrs(org_id: Any, org_slug: str | None = None) -> dict[str, Any]:
    """Attributes that let traces be filtered and costed per org."""
    return {
        "strata.org_id": str(org_id),
        "langfuse.trace.metadata.org_id": str(org_id),
        "langfuse.trace.metadata.org": org_slug,
    }


def record_usage(target: Span, usage: dict[str, int] | None) -> None:
    """Token usage of one model call, in GenAI semantic-convention names (Langfuse
    reads `gen_ai.usage.*`)."""
    if not usage:
        return
    set_attrs(
        target,
        **{
            "gen_ai.usage.input_tokens": usage.get("input"),
            "gen_ai.usage.output_tokens": usage.get("output"),
            "gen_ai.usage.cache_read_input_tokens": usage.get("cache_read") or None,
        },
    )


def start(name: str, kind: str = "span", **attrs: Any) -> Span:
    """A span that isn't made current: for work that has no child spans and may
    straddle `yield`s (a streamed model turn). The caller must `end()` it."""
    return tracer.start_span(name, attributes=_clean({"langfuse.observation.type": kind, **attrs}))
