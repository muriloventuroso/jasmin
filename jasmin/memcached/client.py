from pymemcache.client.base import Client


def ConnectionWithConfiguration(_MemcachedForJasminConfig):
    return Client((_MemcachedForJasminConfig.host, _MemcachedForJasminConfig.port))
