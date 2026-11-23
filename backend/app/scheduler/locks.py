from contextlib import contextmanager
from collections.abc import Iterator
import logging
import uuid

import redis

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class RedisDistributedLock:
    """
    Prevents duplicate scheduled job execution across multiple backend instances.
    Uses atomic Redis SET NX EX and verifies token ownership on release.
    """

    def __init__(self, redis_url: str | None = None) -> None:
        self.redis_url = redis_url or get_settings().redis_url

    @contextmanager
    def acquire(self, lock_name: str, ttl_seconds: int = 120) -> Iterator[bool]:
        token = str(uuid.uuid4())
        key = f"otter:lock:{lock_name}"
        client: redis.Redis | None = None
        acquired = False
        try:
            client = redis.Redis.from_url(
                self.redis_url,
                socket_connect_timeout=1.5,
                socket_timeout=1.5,
            )
            acquired = bool(client.set(key, token, nx=True, ex=ttl_seconds))
        except Exception as exc:
            logger.warning("Redis lock unavailable for %s (%s); skipping distributed lock", lock_name, type(exc).__name__)
            acquired = True  # single-instance fallback when Redis is offline in dev
            client = None

        try:
            yield acquired
        finally:
            if client is not None and acquired:
                try:
                    # Atomic check-and-delete Lua script so we never release another worker's lock
                    script = (
                        "if redis.call('get', KEYS[1]) == ARGV[1] then "
                        "return redis.call('del', KEYS[1]) else return 0 end"
                    )
                    client.eval(script, 1, key, token)
                except Exception:
                    pass
                finally:
                    client.close()
