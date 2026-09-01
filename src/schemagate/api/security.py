import time
from collections import OrderedDict

from schemagate.errors import NotAuthorisedError, RateLimitedError

BEARER = "bearer "

# Stale windows are dropped as they are noticed rather than swept, and the cap
# is what stops a flood of one-request callers from growing the table without
# bound. Losing the oldest window only ever forgives a request.
MAX_TRACKED = 10_000

WINDOW_SECONDS = 60.0


def presented(authorization: str | None, api_key: str | None) -> str | None:
    """The key a caller offered, from either header it may have used.

    `Authorization: Bearer` is what a generated client sends; `X-API-Key` is
    what a hand-written curl usually carries. Both are accepted.
    """
    if authorization and authorization.lower().startswith(BEARER):
        candidate = authorization[len(BEARER) :].strip()
        if candidate:
            return candidate
    if api_key and api_key.strip():
        return api_key.strip()
    return None


def authorise(accepted: bool) -> None:
    """Refuse a caller whose key is not configured.

    The message names neither the key presented nor how many are configured.
    """
    if not accepted:
        raise NotAuthorisedError(
            "This endpoint needs an API key. Send it as `Authorization: Bearer <key>` "
            "or `X-API-Key: <key>`."
        )


class RateLimiter:
    """A fixed window per caller, counted in memory.

    Not a token bucket and not shared between processes. It defends the model
    budget against one caller's mistake, which a count per minute in the
    process doing the spending covers. Four workers allow four times the limit.
    """

    def __init__(self, per_minute: int, window: float = WINDOW_SECONDS) -> None:
        self._per_minute = per_minute
        self._window = window
        self._seen: OrderedDict[str, tuple[int, int]] = OrderedDict()

    @property
    def enabled(self) -> bool:
        return self._per_minute > 0

    def check(self, who: str, now: float | None = None) -> None:
        """Count one request, refusing the one past the allowance."""
        if not self.enabled:
            return

        moment = time.monotonic() if now is None else now
        window = int(moment // self._window)

        seen_window, count = self._seen.get(who, (window, 0))
        if seen_window != window:
            count = 0

        count += 1
        self._seen[who] = (window, count)
        self._seen.move_to_end(who)
        while len(self._seen) > MAX_TRACKED:
            self._seen.popitem(last=False)

        if count > self._per_minute:
            raise RateLimitedError(
                f"Rate limit of {self._per_minute} requests per minute reached. "
                f"Wait for the next minute, or raise "
                f"SCHEMAGATE_RATE_LIMIT_PER_MINUTE."
            )
