# vim:set expandtab:
# vim:set foldmethod=indent:
# vim:set shiftwidth=4:
# vim:set tabstop=4:

from __future__ import generators
import ConfigParser
import copy
import email.Message
import email.Parser
import inspect
import md5
import os
import popen2
import random
import re
import select
import sha
import shutil
import socket
import sys
import time
import isconf
from isconf.Errno import iserrno
from isconf.Globals import *
from isconf import ISFS
from isconf.Kernel import kernel, Bus
from isconf.fbp822 import fbp822, Error822

class CLIServerFactory:

    def __init__(self,socks):
        self.socks = socks

    def run(self):
        while True:
            slist = []
            yield self.socks.rx(slist)
            for sock in slist:
                server = CLIServer(sock=sock)
                kernel.spawn(server.run())

class CLIServer:

    def __init__(self,sock):
        self.transport=sock
        self.verbose = False
        self.debug = False

    def run(self):
        yield kernel.sigbusy # speed things up a bit
        debug("CLIServer running")
        fbp = fbp822()

        # set up FBP buses
        tocli = Bus('tocli')

        # process messages from client
        proc = kernel.spawn(self.process(transport=self.transport,outpin=tocli))
        # send messages to client
        res = kernel.spawn(self.respond(transport=self.transport,inpin=tocli))
        # merge in log messages
        log = kernel.spawn(self.merge(tocli,BUS.log))

        # wait for everything to quiesce
        yield kernel.siguntil, proc.isdone
        yield kernel.sigsleep, .1
        while True:
            yield None
            i=0
            for q in (tocli,BUS.log):
                if q.busy():
                    i+=1
                    continue
            if i == 0: 
                break

        debug("telling client to exit")
        yield kernel.sigsleep, .1 # XXX 
        tocli.tx(fbp.mkmsg('rc',0))
        # tocli.close()

    def merge(self,outbus,inbus):
        while True:
            mlist = []
            yield inbus.rx(mlist)
            for msg in mlist:
                if msg in (kernel.eagain,None):
                    continue
                if outbus.state == 'down':
                    return
                if msg is kernel.eof:
                    return
                outbus.tx(msg)

    def process(self,transport,outpin):
        while True:
            yield None
            # read messages from client
            mlist = FBP.fromFile(stream=transport)
            for msg in mlist:
                if msg in (kernel.eagain,None):
                    continue
                if outpin.state == 'down':
                    return
                if msg is kernel.eof:
                    # outpin.close()
                    return
                debug("from client:", str(msg))
                rectype = msg.type()
                if rectype != 'cmd':
                    error(iserrno.EINVAL, 
                        "first message must be cmd, got %s" % rectype)
                    return
                self.verbose = msg.head.verbose
                self.debug = msg.head.debug
                data = msg.payload()
                opt = dict(msg.items())
                debug(opt)
                verb = msg['verb']
                if verb != 'lock' and opt['message'] != 'None':
                    error(iserrno.EINVAL, "-m is only valid on lock")
                if verb == 'exec': verb = 'Exec' # sigh
                if verb == 'shutdown': 
                    kernel.shutdown()
                    return
                args=[]
                if len(data):
                    data = data.strip()
                    args = data.split('\n')
                ops = Ops(opt=opt,args=args,data=data,outpin=outpin)
                try:
                    func = getattr(ops,verb)
                except AttributeError:
                    error(outpin,iserrno.EINVAL,verb)
                    return
                # start command processor, wait for it to finish
                yield kernel.wait(func())
                return # XXX can't handle stdin yet

    def respond(self,transport,inpin):
        while True:
            mlist = []
            yield inpin.rx(mlist)
            for msg in mlist:
                # if not hasattr(msg,'type'):
                if transport.state == 'down':
                    return
                if msg in (kernel.eagain,None):
                    continue
                if msg is kernel.eof:
                    # transport.close()
                    return
                # no logging in here!  causes a message loop...
                # debug("to client:", str(msg))
                rectype = msg.type()
                if rectype == 'debug' and not self.debug:
                    continue
                transport.write(str(msg))
                if msg.type() == 'rc':
                    transport.close()
                    return


class Ops:
    """ISconf server-side operations
    
    Each of these tasks *must* continue running until their operations
    are complete.  They or their called routines can send a non-zero rc
    message to outpin.  If they don't, process() will follow up with a
    zero rc.  Whichever rc message arrives at respond() first wins.
    
    """

    def __init__(self,opt,args,data,outpin):
        self.opt = opt
        self.args = args
        self.data = data
        self.outpin = outpin

        self.volname = branch()
        logname = self.opt['logname']
        self.volume = ISFS.Volume(self.volname,logname=logname)

    def ci(self):
        yield kernel.wait(self.volume.ci())

    def Exec(self):
        yield None
        cwd = self.opt['cwd']
        if not len(self.data):
            error(iserrno.EINVAL,"missing exec command")
            return
        self.volume.Exec(self.data,cwd)


    def lock(self):
        if not self.opt['message']:
            error(iserrno.NEEDMSG,'did not lock %s' % self.volname)
            return
        yield kernel.wait(self.volume.lock(self.opt['message']))

    def snap(self):
        # XXX move most of this to ISFS
        debug("starting snap")
        yield None
        if not len(self.args):
            error(iserrno.EINVAL,"missing snapshot pathname")
            return
        if len(self.args) > 1:
            error(iserrno.EINVAL,
                    "can only snapshot one file at a time (for now)")
            return
        path = self.args[0]
        cwd = self.opt['cwd']
        path = os.path.join(cwd,path)
        if not os.path.exists(path):
            error(iserrno.ENOENT,path)
            return
        if not os.path.isfile(path):
            error(iserrno.EINVAL,"%s is not a file" % path)
            return
        st = os.stat(path)
        src = open(path,'r')
        debug("calling open")
        dst = self.volume.open(path,'w')
        if not dst:
            return
        dst.setstat(st)
        while True:
            data = src.read(1024 * 1024 * 1)
            if not len(data):
                break
            dst.write(data)
        src.close()
        debug("calling close")
        dst.close()

    def unlock(self):
        yield None
        locker = self.volume.lockedby()
        self.volume.unlock()
        info("broke %s lock -- please notify %s" % (self.volname,locker))

    def up(self):
        yield kernel.wait(self.volume.update())

            
def branch(val=None):
    varisconf = os.environ['VARISCONF']
    fname = "%s/branch" % varisconf
    if not os.path.exists(fname):
        val = 'generic'
    if val is not None:
        open(fname,'w').write(val)
    val = open(fname,'r').read()
    return val

def XXXbusexit(errpin,code,msg=''):
    desc = iserrno.strerror(code)
    if str or msg:
        msg = "%s: %s\n" % (str(msg), desc)
    fbp=fbp822()
    if msg and code:
        warn("busexit: ", msg)
        msg = "isconf: error: " + msg
        errpin.tx(fbp.mkmsg('stderr',msg))
    errpin.tx(fbp.mkmsg('rc',code))
    # errpin.close()

def client(transport,argv,kwopt):

    """
    A unix-domain client of an isconf server.  This client is very
    thin -- all the smarts are on the server side.

    argv is e.g. ('snap', '/tmp/foo') 
    """

    # XXX convert to use global log funcs
    def clierr(code,msg=''):
        desc = iserrno.strerror(code)
        msg = "%s: %s" % (msg, desc)
        warn("clierr: ", msg)
        return code

    fbp = fbp822()
    verb = argv.pop(0)
    if len(argv):
        payload = "\n".join(argv) + "\n"
    else:
        payload = ''
    logname = os.environ['LOGNAME']
    cwd = os.getcwd()
    msg = fbp.mkmsg('cmd',payload,verb=verb,logname=logname,cwd=cwd,**kwopt)

    # this is a blocking write...
    transport.write(str(msg))

    # sockfile = transport.sock.makefile('r')
    # stream = fbp.fromFile(sockfile,intask=False)
    stream = fbp.fromStream(transport,intask=False)
    # process one message each time through loop
    while True:
        time.sleep(.1)
        try:
            msg = stream.next()
        except StopIteration:
            return clierr(iserrno.ECONNRESET)
        except Error822, e:
            return clierr(iserrno.EBADMSG,e)
        if msg in (kernel.eagain,None,kernel.sigbusy):
            continue
        rectype = msg.type()
        data = msg.payload()
        if rectype == 'rc':
            code = int(data)
            return code
        elif rectype == 'stdout': sys.stdout.write(data)
        elif rectype == 'stderr': sys.stderr.write(data)
        elif rectype == 'reqstdin':
            for line in sys.stdin:
                msg = fbp.mkmsg('stdin',line)
                transport.write(str(msg))
            transport.shutdown()
        elif rectype == 'debug':
            debug(data)
        elif rectype == 'info':
            info(data)
        elif rectype == 'warn':
            warn(data)
        elif rectype == 'error':
            error(msg.head.rc,data)
        elif hasattr(os.environ,'DEBUG'):
            debug(str(msg))
        
