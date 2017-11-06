"""
Config file handler for 'redis-client' section in jasmin.cfg
"""

import os

from jasmin.config.tools import ConfigFile

# Related to travis-ci builds
ROOT_PATH = os.getenv('ROOT_PATH', '/')


class MemcachedForJasminConfig(ConfigFile):
    """Config handler for 'redis-client' section"""

    def __init__(self, config_file=None):
        ConfigFile.__init__(self, config_file)

        self.host = self._get('memcached-client', 'host', '127.0.0.1')
        self.port = self._getint('memcached-client', 'port', 11211)
