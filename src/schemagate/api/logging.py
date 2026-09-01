import logging
import time
import uuid
from collections.abc import Awaitable, Callable

from starlette.requests import Request
from starlette.responses import Response

log = logging.getLogger("schemagate")

REQUEST_ID_HEADER = "x-request-id"


async def record_requests(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Log one line per request, with what it did and how long it took.

    What is deliberately absent is as important as what is here. No connection
    string, no API key, and no document content: the file belongs to whoever
    uploaded it, and a log is the easiest place for it to end up somewhere it
    was never meant to go. Table names and row counts are enough to answer the
    questions an operator actually has.
    """
    request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex[:12]
    request.state.request_id = request_id
    request.state.outcome = {}

    started = time.perf_counter()
    response = await call_next(request)
    duration = int((time.perf_counter() - started) * 1000)

    response.headers[REQUEST_ID_HEADER] = request_id

    if request.url.path not in {"/health", "/icon.png"}:
        log.info(
            "%s %s %s",
            request.method,
            request.url.path,
            response.status_code,
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": duration,
                **request.state.outcome,
            },
        )
    return response


def note(request: Request, **facts: object) -> None:
    """Add facts to the line this request will log.

    Called from the handler, which is the only place that knows what the work
    turned out to be.
    """
    outcome = getattr(request.state, "outcome", None)
    if outcome is not None:
        outcome.update(facts)
