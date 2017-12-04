#!/usr/bin/python

import os
import signal
import sys
import syslog

from lockfile import FileLock, LockTimeout, AlreadyLocked
from twisted.cred import portal
from twisted.cred.checkers import AllowAnonymousAccess, InMemoryUsernamePasswordDatabaseDontUse
from twisted.internet import reactor, defer
from twisted.python import usage
from twisted.spread import pb
from twisted.web import server

from jasmin.interceptor.configs import InterceptorPBClientConfig
from jasmin.interceptor.proxies import InterceptorPBProxy
from jasmin.managers.clients import SMPPClientManagerPB
from jasmin.managers.configs import SMPPClientPBConfig
from jasmin.protocols.cli.configs import JCliConfig
from jasmin.protocols.cli.factory import JCliFactory
from jasmin.protocols.http.configs import HTTPApiConfig
from jasmin.protocols.http.server import HTTPApi
from jasmin.protocols.smpp.configs import SMPPServerConfig, SMPPServerPBConfig
from jasmin.protocols.smpp.factory import SMPPServerFactory
from jasmin.protocols.smpp.pb import SMPPServerPB
from jasmin.queues.configs import AmqpConfig
from jasmin.queues.factory import AmqpFactory
from jasmin.redis.client import ConnectionWithConfiguration
from jasmin.redis.configs import RedisForJasminConfig
from jasmin.routing.configs import RouterPBConfig, deliverSmThrowerConfig
from jasmin.routing.router import RouterPB
from jasmin.routing.throwers import deliverSmThrower
from jasmin.tools.cred.checkers import RouterAuthChecker
from jasmin.tools.cred.portal import JasminPBRealm
from jasmin.tools.cred.portal import SmppsRealm
from jasmin.tools.spread.pb import JasminPBPortalRoot

# Related to travis-ci builds
ROOT_PATH = os.getenv('ROOT_PATH', '/')


class Options(usage.Options):
    optParameters = [
        ['config', 'c', '%s/etc/jasmin/jasmin.cfg' % ROOT_PATH,
         'Jasmin configuration file'],
        ['username', 'u', None,
         'jCli username used to load configuration profile on startup'],
        ['password', 'p', None,
         'jCli password used to load configuration profile on startup'],
    ]

    optFlags = [
        ['disable-smpp-server', None, 'Do not start SMPP Server service'],
        ['enable-dlr-thrower', None, 'Enable DLR Thrower service (not recommended: start the dlrd daemon instead)'],
        ['enable-dlr-lookup', None, 'Enable DLR Lookup service (not recommended: start the dlrlookupd daemon instead)'],
        # @TODO: deliver-thrower must be executed as a standalone process, just like dlr-thrower
        ['disable-deliver-thrower', None, 'Do not DeliverSm Thrower service'],
        ['disable-http-api', None, 'Do not HTTP API'],
        ['disable-jcli', None, 'Do not jCli console'],
        ['enable-interceptor-client', None, 'Start Interceptor client'],
    ]


class JasminDaemon(object):
    def __init__(self, opt):
        self.options = opt
        self.components = {}

    @defer.inlineCallbacks
    def startRedisClient(self):
        """Start AMQP Broker"""
        RedisForJasminConfigInstance = RedisForJasminConfig(self.options['config'])
        self.components['rc'] = yield ConnectionWithConfiguration(RedisForJasminConfigInstance)
        # Authenticate and select db
        if RedisForJasminConfigInstance.password is not None:
            yield self.components['rc'].auth(RedisForJasminConfigInstance.password)
            yield self.components['rc'].select(RedisForJasminConfigInstance.dbid)

    def stopRedisClient(self):
        """Stop Redis Server"""
        return self.components['rc'].disconnect()

    def startAMQPBrokerService(self):
        """Start AMQP Broker"""

        AMQPServiceConfigInstance = AmqpConfig(self.options['config'])
        self.components['amqp-broker-factory'] = AmqpFactory(AMQPServiceConfigInstance)
        self.components['amqp-broker-factory'].preConnect()

        # Add service
        self.components['amqp-broker-client'] = reactor.connectTCP(
            AMQPServiceConfigInstance.host,
            AMQPServiceConfigInstance.port,
            self.components['amqp-broker-factory'])

    def stopAMQPBrokerService(self):
        """Stop AMQP Broker"""

        return self.components['amqp-broker-client'].disconnect()

    def startRouterPBService(self):
        """Start Router PB server"""

        RouterPBConfigInstance = RouterPBConfig(self.options['config'])
        self.components['router-pb-factory'] = RouterPB(RouterPBConfigInstance)

        # Set authentication portal
        p = portal.Portal(JasminPBRealm(self.components['router-pb-factory']))
        if RouterPBConfigInstance.authentication:
            c = InMemoryUsernamePasswordDatabaseDontUse()
            c.addUser(RouterPBConfigInstance.admin_username,
                      RouterPBConfigInstance.admin_password)
            p.registerChecker(c)
        else:
            p.registerChecker(AllowAnonymousAccess())
        jPBPortalRoot = JasminPBPortalRoot(p)

        # AMQP Broker is used to listen to deliver_sm/dlr queues
        return self.components['router-pb-factory'].addAmqpBroker(self.components['amqp-broker-factory'])

    def startSMPPClientManagerPBService(self):
        """Start SMPP Client Manager PB server"""

        SMPPClientPBConfigInstance = SMPPClientPBConfig(self.options['config'])
        self.components['smppcm-pb-factory'] = SMPPClientManagerPB(SMPPClientPBConfigInstance)

        # Set authentication portal
        p = portal.Portal(JasminPBRealm(self.components['smppcm-pb-factory']))
        if SMPPClientPBConfigInstance.authentication:
            c = InMemoryUsernamePasswordDatabaseDontUse()
            c.addUser(SMPPClientPBConfigInstance.admin_username, SMPPClientPBConfigInstance.admin_password)
            p.registerChecker(c)
        else:
            p.registerChecker(AllowAnonymousAccess())
        jPBPortalRoot = JasminPBPortalRoot(p)

        # AMQP Broker is used to listen to submit_sm queues and publish to deliver_sm/dlr queues
        self.components['smppcm-pb-factory'].addAmqpBroker(self.components['amqp-broker-factory'])
        self.components['smppcm-pb-factory'].addRedisClient(self.components['rc'])
        self.components['smppcm-pb-factory'].addRouterPB(self.components['router-pb-factory'])

        # Add interceptor if enabled:
        if 'interceptor-pb-client' in self.components:
            self.components['smppcm-pb-factory'].addInterceptorPBClient(
                self.components['interceptor-pb-client'])

    def startHTTPApiService(self):
        """Start HTTP Api"""

        httpApiConfigInstance = HTTPApiConfig(self.options['config'])

        # Add interceptor if enabled:
        if 'interceptor-pb-client' in self.components:
            interceptorpb_client = self.components['interceptor-pb-client']
        else:
            interceptorpb_client = None

        self.components['http-api-factory'] = HTTPApi(
            self.components['router-pb-factory'],
            self.components['smppcm-pb-factory'],
            httpApiConfigInstance,
            interceptorpb_client)

        self.components['http-api-server'] = reactor.listenTCP(
            httpApiConfigInstance.port,
            server.Site(self.components['http-api-factory'], logPath=httpApiConfigInstance.access_log),
            interface=httpApiConfigInstance.bind)

    def stopHTTPApiService(self):
        """Stop HTTP Api"""
        return self.components['http-api-server'].stopListening()

    def startInterceptorPBClient(self):
        """Start Interceptor client"""

        InterceptorPBClientConfigInstance = InterceptorPBClientConfig(self.options['config'])
        self.components['interceptor-pb-client'] = InterceptorPBProxy()

        return self.components['interceptor-pb-client'].connect(
            InterceptorPBClientConfigInstance.host,
            InterceptorPBClientConfigInstance.port,
            InterceptorPBClientConfigInstance.username,
            InterceptorPBClientConfigInstance.password,
            retry=True)

    def stopInterceptorPBClient(self):
        """Stop Interceptor client"""

        if self.components['interceptor-pb-client'].isConnected:
            return self.components['interceptor-pb-client'].disconnect()

    @defer.inlineCallbacks
    def start(self):
        """Start Jasmind daemon"""
        syslog.syslog(syslog.LOG_INFO, "Starting Jasmin Daemon ...")

        # Requirements check begin:
        ########################################################
        if self.options['enable-interceptor-client']:
            try:
                # [optional] Start Interceptor client
                yield self.startInterceptorPBClient()
            except Exception, e:
                syslog.syslog(syslog.LOG_ERR, "  Cannot connect to interceptor: %s" % e)
            else:
                syslog.syslog(syslog.LOG_INFO, "  Interceptor client Started.")
        # Requirements check end.

        ########################################################
        # Connect to redis server
        try:
            yield self.startRedisClient()
        except Exception, e:
            syslog.syslog(syslog.LOG_ERR, "  Cannot start RedisClient: %s" % e)
        else:
            syslog.syslog(syslog.LOG_INFO, "  RedisClient Started.")

        ########################################################
        # Start AMQP Broker
        try:
            self.startAMQPBrokerService()
            yield self.components['amqp-broker-factory'].getChannelReadyDeferred()
        except Exception, e:
            syslog.syslog(syslog.LOG_ERR, "  Cannot start AMQP Broker: %s" % e)
        else:
            syslog.syslog(syslog.LOG_INFO, "  AMQP Broker Started.")

        ########################################################
        # Start Router PB server
        try:
            yield self.startRouterPBService()
        except Exception, e:
            syslog.syslog(syslog.LOG_ERR, "  Cannot start RouterPB: %s" % e)
        else:
            syslog.syslog(syslog.LOG_INFO, "  RouterPB Started.")

        ########################################################
        # Start SMPP Client connector manager and add rc
        try:
            self.startSMPPClientManagerPBService()
        except Exception, e:
            syslog.syslog(syslog.LOG_ERR, "  Cannot start SMPPClientManagerPB: %s" % e)
        else:
            syslog.syslog(syslog.LOG_INFO, "  SMPPClientManagerPB Started.")

        ########################################################
        if not self.options['disable-http-api']:
            try:
                # [optional] Start HTTP Api
                self.startHTTPApiService()
            except Exception, e:
                syslog.syslog(syslog.LOG_ERR, "  Cannot start HTTPApi: %s" % e)
            else:
                syslog.syslog(syslog.LOG_INFO, "  HTTPApi Started.")

    @defer.inlineCallbacks
    def stop(self):
        """Stop Jasmind daemon"""
        syslog.syslog(syslog.LOG_INFO, "Stopping Jasmin Daemon ...")

        if 'http-api-server' in self.components:
            yield self.stopHTTPApiService()
            syslog.syslog(syslog.LOG_INFO, "  HTTPApi stopped.")

        if 'amqp-broker-client' in self.components:
            yield self.stopAMQPBrokerService()
            syslog.syslog(syslog.LOG_INFO, "  AMQP Broker disconnected.")

        if 'rc' in self.components:
            yield self.stopRedisClient()
            syslog.syslog(syslog.LOG_INFO, "  RedisClient stopped.")

        # Shutdown requirements:
        if 'interceptor-pb-client' in self.components:
            yield self.stopInterceptorPBClient()
            syslog.syslog(syslog.LOG_INFO, "  Interceptor client stopped.")

        reactor.stop()

    def sighandler_stop(self, signum, frame):
        """Handle stop signal cleanly"""
        syslog.syslog(syslog.LOG_INFO, "Received signal to stop Jasmin Daemon")

        return self.stop()


if __name__ == '__main__':
    lock = None
    try:
        options = Options()
        options.parseOptions()

        # Must not be executed simultaneously (c.f. #265)
        lock = FileLock("/tmp/httpapid")

        # Ensure there are no paralell runs of this script
        lock.acquire(timeout=2)

        # Prepare to start
        ja_d = JasminDaemon(options)
        # Setup signal handlers
        signal.signal(signal.SIGINT, ja_d.sighandler_stop)
        # Start JasminDaemon
        ja_d.start()

        reactor.run()
    except usage.UsageError, errortext:
        print '%s: %s' % (sys.argv[0], errortext)
        print '%s: Try --help for usage details.' % (sys.argv[0])
    except LockTimeout:
        print "Lock not acquired ! exiting"
    except AlreadyLocked:
        print "There's another instance on jasmind running, exiting."
    finally:
        # Release the lock
        if lock is not None and lock.i_am_locking():
            lock.release()
