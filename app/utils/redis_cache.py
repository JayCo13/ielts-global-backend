import json
import os
from typing import Any, Optional, Union
import redis.asyncio as redis
from redis.asyncio import Redis
import logging

logger = logging.getLogger(__name__)

class RedisCache:
    def __init__(self):
        self.redis_url = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
        self.redis_client: Optional[Redis] = None
        self.default_ttl = 3600  # 1 hour default TTL
        
    async def connect(self):
        """Initialize Redis connection"""
        try:
            self.redis_client = redis.from_url(
                self.redis_url,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
                retry_on_timeout=True,
                health_check_interval=30
            )
            # Test connection
            await self.redis_client.ping()
            logger.info("Redis connection established successfully")
        except Exception as e:
            logger.error(f"Failed to connect to Redis: {e}")
            self.redis_client = None
            
    async def disconnect(self):
        """Close Redis connection"""
        if self.redis_client:
            await self.redis_client.close()
            logger.info("Redis connection closed")
            
    async def get(self, key: str) -> Optional[Any]:
        """Get value from cache"""
        if not self.redis_client:
            return None
            
        try:
            value = await self.redis_client.get(key)
            if value:
                return json.loads(value)
            return None
        except Exception as e:
            logger.error(f"Redis GET error for key {key}: {e}")
            return None
            
    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        """Set value in cache with TTL"""
        if not self.redis_client:
            return False
            
        try:
            ttl = ttl or self.default_ttl
            serialized_value = json.dumps(value, default=str)
            await self.redis_client.setex(key, ttl, serialized_value)
            return True
        except Exception as e:
            logger.error(f"Redis SET error for key {key}: {e}")
            return False
            
    async def delete(self, key: str) -> bool:
        """Delete key from cache"""
        if not self.redis_client:
            return False
            
        try:
            result = await self.redis_client.delete(key)
            return result > 0
        except Exception as e:
            logger.error(f"Redis DELETE error for key {key}: {e}")
            return False
            
    async def exists(self, key: str) -> bool:
        """Check if key exists in cache"""
        if not self.redis_client:
            return False
            
        try:
            result = await self.redis_client.exists(key)
            return result > 0
        except Exception as e:
            logger.error(f"Redis EXISTS error for key {key}: {e}")
            return False
            
    async def clear_pattern(self, pattern: str) -> int:
        """Clear all keys matching pattern"""
        if not self.redis_client:
            return 0
            
        try:
            keys = await self.redis_client.keys(pattern)
            if keys:
                return await self.redis_client.delete(*keys)
            return 0
        except Exception as e:
            logger.error(f"Redis CLEAR_PATTERN error for pattern {pattern}: {e}")
            return 0
            
    async def increment(self, key: str, amount: int = 1, ttl: Optional[int] = None) -> Optional[int]:
        """Increment counter with optional TTL"""
        if not self.redis_client:
            return None
            
        try:
            pipe = self.redis_client.pipeline()
            pipe.incr(key, amount)
            if ttl:
                pipe.expire(key, ttl)
            results = await pipe.execute()
            return results[0]
        except Exception as e:
            logger.error(f"Redis INCREMENT error for key {key}: {e}")
            return None

# Global cache instance
cache = RedisCache()

# Cache key generators
def get_exam_cache_key(exam_id: int) -> str:
    return f"exam:{exam_id}"
    
def get_reading_test_cache_key(test_id: int) -> str:
    return f"reading_test:{test_id}"
    
def get_listening_test_cache_key(test_id: int) -> str:
    return f"listening_test:{test_id}"
    
def get_audio_metadata_cache_key(audio_id: int) -> str:
    return f"audio_metadata:{audio_id}"
    
def get_user_session_cache_key(user_id: int) -> str:
    return f"user_session:{user_id}"
    
def get_exam_results_cache_key(user_id: int, exam_id: int) -> str:
    return f"exam_results:{user_id}:{exam_id}"

# Cache decorators
def cache_result(key_func, ttl: int = 3600):
    """Decorator to cache function results"""
    def decorator(func):
        async def wrapper(*args, **kwargs):
            # Generate cache key
            cache_key = key_func(*args, **kwargs)
            
            # Try to get from cache
            cached_result = await cache.get(cache_key)
            if cached_result is not None:
                return cached_result
                
            # Execute function and cache result
            result = await func(*args, **kwargs)
            if result is not None:
                await cache.set(cache_key, result, ttl)
                
            return result
        return wrapper
    return decorator
