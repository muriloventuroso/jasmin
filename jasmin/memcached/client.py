import json
from datetime import date, datetime
from pymemcache.client.base import Client


def json_serializer(key, value):
    if type(value) == str:
        return value, 1
    if isinstance(value, (datetime, date)):
        return str(value), 3
    return json.dumps(value), 2


def json_deserializer(key, value, flags):
    if flags == 1:
        return value
    if flags == 2:
        return json.loads(value)
    if flags == 3:
        return datetime.datetime.strptime(value, '%Y-%m-%d %H:%M:%S.%f')
    raise Exception("Unknown serialization format")


def ConnectionWithConfiguration(_MemcachedForJasminConfig):
    return Client(
        (_MemcachedForJasminConfig.host, _MemcachedForJasminConfig.port), serializer=json_serializer,
        deserializer=json_deserializer)
