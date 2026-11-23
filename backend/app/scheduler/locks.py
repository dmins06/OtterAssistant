from contextlib import contextmanager
from collections.abc import Iterator
import logging
import threading
import uuid
from typing import Any

import redis

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_RENEW_LUA_SCRIPT = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then "
    "return redis.call('pexpire', KEYS[1], ARGV[2]) else return 0 end"
)

_RELEASE_LUA_SCRIPT = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then "
    "return redis.call('del', KEYS[1]) else return 0 end"
)


class LockLease:
    """
    Represents an acquired (or denied) distributed lock lease with ownership token,
    explicit renewal, and safe lock-loss detection.
    Truthiness (`if lease:`) remains backward-compatible with `bool` callers.
    """

    def __init__(
        self,
        *,
        acquired: bool,
        key: str,
        token: str,
        ttl_seconds: int,
        client: Any | None = None,
    ) -> None:
        self.acquired = acquired
        self.key = key
        self.token = token
        self.ttl_seconds = ttl_seconds
        self._client = client
        self._lost = False
        self._lock = threading.Lock()

    @property
    def lost(self) -> bool:
        with self._lock:
            return self._lost

    def mark_lost(self) -> None:
        with self._lock:
            self._lost = True

    def renew(self) -> bool:
        """Atomically extend TTL only if this lease still owns the lock token."""
        if not self.acquired or self.lost:
            return False
        if self._client is None:
            return True  # single-instance fallback when Redis is unavailable in dev
        try:
            ttl_ms = max(1000, int(self.ttl_seconds * 1000))
            renewed = bool(
                self._client.eval(_RENEW_LUA_SCRIPT, 1, self.key, self.token, ttl_ms)
            )
            if not renewed:
                self.mark_lost()
                logger.warning("Distributed lock ownership lost during renewal for key=%s", self.key)
            return renewed
        except Exception as exc:
            self.mark_lost()
            logger.warning(
                "Distributed lock renewal failed for key=%s (%s)",
                self.key,
                type(exc).__name__,
            )
            return False

    def __bool__(self) -> bool:
        return self.acquired and not self.lost


class RedisDistributedLock:
    """
    Prevents duplicate scheduled job or concurrent per-user sync execution across workers.
    Features:
      - Unique UUID ownership token per acquisition
      - Atomic Redis SET NX EX acquisition
      - Optional background heartbeat renewal (TTL / 3) with safe lock-loss detection
      - Atomic Lua check-and-delete release so another worker's lock is never deleted
    """

    def __init__(
        self,
        redis_url: str | None = None,
        redis_client: Any | None = None,
    ) -> None:
        self.redis_url = redis_url or get_settings().redis_url
        self._injected_client = redis_client

    @contextmanager
    def acquire(
        self,
        lock_name: str,
        ttl_seconds: int = 120,
        auto_renew: bool = True,
        renew_interval_seconds: float | None = None,
    ) -> Iterator[LockLease]:
        token = str(uuid.uuid4())
        key = f"otter:lock:{lock_name}"
        client: Any | None = None
        owns_client = False
        acquired = False

        try:
            if self._injected_client is not None:
                client = self._injected_client
            else:
                client = redis.Redis.from_url(
                    self.redis_url,
                    socket_connect_timeout=1.5,
                    socket_timeout=1.5,
                )
                owns_client = True
            acquired = bool(client.set(key, token, nx=True, ex=ttl_seconds))
        except Exception as exc:
            logger.warning(
                "Redis lock unavailable for %s (%s); falling back to single-instance execution",
                lock_name,
                type(exc).__name__,
            )
            acquired = True
            if owns_client and client is not None:
                try:
                    client.close()
                except Exception:
                    pass
            client = None
            owns_client = False

        lease = LockLease(
            acquired=acquired,
            key=key,
            token=token,
            ttl_seconds=ttl_seconds,
            client=client,
        )

        stop_event = threading.Event()
        heartbeat_thread: threading.Thread | None = None
        if acquired and auto_renew and client is not None:
            interval = (
                renew_interval_seconds
                if renew_interval_seconds is not None
                else max(1.0, ttl_seconds / 3.0)
            )

            def _heartbeat() -> None:
                while not stop_event.wait(interval):
                    if not lease.renew():
                        break

            heartbeat_thread = threading.Thread(
                target=_heartbeat,
                name=f"lock-renew-{lock_name}",
                daemon=True,
            )
            heartbeat_thread.start()

        try:
            yield lease
        finally:
            stop_event.set()
            if heartbeat_thread is not None:
                heartbeat_thread.join(timeout=1.0)
            if client is not None and acquired:
                try:
                    client.eval(_RELEASE_LUA_SCRIPT, 1, key, token)
                except Exception:
                    pass
                finally:
                    if owns_client:
                        try:
                            client.close()
                        except Exception:
                            pass
