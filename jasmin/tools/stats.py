from datetime import datetime, time


class KeyNotFound(Exception):
    """
    Raised when setting or getting an unknown statistics key
    """


class KeyNotIncrementable(Exception):
    """
    Raised when trying to increment a non integer key
    """


class Stats(object):
    def set(self, key, value):
        if key not in self._stats:
            raise KeyNotFound(key)

        self._stats[key] = value

    def get(self, key):
        if key not in self._stats:
            raise KeyNotFound(key)

        return self._stats[key]

    def inc(self, key, inc=1):
        if key not in self._stats:
            raise KeyNotFound(key)
        if not isinstance(self._stats[key], int):
            raise KeyNotIncrementable(key)

        self._stats[key] += inc

    def dec(self, key, inc=1):
        if key not in self._stats:
            raise KeyNotFound(key)
        if not isinstance(self._stats[key], int):
            raise KeyNotIncrementable(key)

        self._stats[key] -= inc


class StatsRedis(object):
    def __init__(self, key, RedisClient, expire=True):
        self.redisClient = RedisClient
        self.key = key
        self.expire = expire

    def set(self, key, value):
        gwdaily = self.redisClient.hgetall(self.key)
        if not gwdaily:
            self.redisClient.hset(self.key, str(key), value)
            if self.expire:
                self.redisClient.expireat(self.key, datetime.combine(datetime.now(), time.max))

    def get(self, key):

        exists = self.redisClient.exists(self.key)
        if not exists:
            self.redisClient.hset(self.key, str(key), 0)
            if self.expire:
                self.redisClient.expireat(self.key, datetime.combine(datetime.now(), time.max))
        return self.redisClient.hget(self.key, key)

    def inc(self, key, inc=1):

        exists = self.redisClient.exists(self.key)
        if not exists:
            self.redisClient.hset(self.key, str(key), 0)
            if self.expire:
                self.redisClient.expireat(self.key, datetime.combine(datetime.now(), time.max))
        self.redisClient.hincrby(self.key, str(key), inc)

    def dec(self, key, inc=1):
        exists = self.redisClient.exists(self.key)
        if not exists:
            self.redisClient.hset(self.key, str(key), 0)
            if self.expire:
                self.redisClient.expireat(self.key, datetime.combine(datetime.now(), time.max))
        self.redisClient.hincrby(self.key, str(key), -inc)
