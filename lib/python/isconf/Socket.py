
from __future__ import generators
import errno
import os
import select
import socket
from isconf.Globals import *
from isconf.Kernel import kernel

class ServerFactory:

    def run(self,out):
        """FBP component; emits ServerSocket refs on the 'out' pin""" 
        while True:
            yield None
            try:
                # accept new connections
                (peersock, address) = self.sock.accept()
                sock = ServerSocket(sock=peersock,address=address)
                yield kernel.sigspawn, sock.run()
                out.tx(sock)
            except socket.error, (error, strerror):
                if not error == errno.EAGAIN:
                    raise
            
class ServerSocket:
    """a TCP or UNIX domain server socket"""

    def __init__(self,sock,address,chunksize=4096):
        self.chunksize = chunksize
        self.sock = sock
        self.address = address
        self.role = 'master'
        self.state = 'up'
        self.txd = ''
        self.rxd = ''
        self.protocol = None
    
    def abort(self,msg=''):
        self.write(msg + "\n")
        self.close()

    def msg(self,msg):
        self.write(msg + "\n")

    def close(self):
        self.state = 'closing'

    def read(self,size):
        actual = min(size,len(self.rxd))
        if actual == 0:
            return ''
        print repr(actual)
        rxd = self.rxd[:actual]
        # print "reading", rxd
        self.rxd = self.rxd[actual:]
        return rxd
    
    def write(self,data):
        # print "writing", repr(data)
        self.txd += data
    
    def shutdown(self):
        self.sock.shutdown(1)

    def run(self,*args,**kwargs):
        busy = False
        while True:
            if busy:
                yield kernel.sigbusy
            else:
                yield None
            # XXX peer timeout ck
            busy = False

            # find pending reads and writes 
            s = self.sock
            try:
                (readable, writeable, inerror) = \
                    select.select([s],[s],[s],0)
            except Exception, e:
                debug("socket exception", e)
                inerror = [s]
        
            # handle errors
            if s in inerror or self.state == 'close':
                try:
                    s.close()
                except:
                    pass
                self.state = 'down'
                break

            # do reads
            if s in readable:
                # read a chunk
                try:
                    rxd = self.sock.recv(self.chunksize)
                except:
                    pass
                # print "receiving", rxd
                self.rxd += rxd
                if self.rxd:
                    busy = True
                else:
                    try:
                        s.shutdown(0)
                    except:
                        pass
                    self.state = 'closing'

            # do writes
            if s in writeable:
                if len(self.txd) <= 0:
                    if self.state == 'closing':
                        self.state = 'close'
                    continue
                # print "sending", self.txd
                try:
                    sent = self.sock.send(self.txd)
                    # print "sent " + self.txd
                except:
                    try:
                        s.shutdown(1)
                    except:
                        pass
                    self.state = 'closing'
                if sent:
                    busy = True
                    # txd is a fifo -- clear as we send bytes off the front
                    self.txd = self.txd[sent:]
                
class TCPServerFactory(ServerFactory):

    def __init__(self, port, chunksize=4096):
        self.chunksize = chunksize
        self.port = port
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, True)
        self.sock.setblocking(0)
        self.sock.bind(('', self.port))     
        self.sock.listen(5)
        info("TCP server listening on port %d" % port)
    
class UNIXServerFactory(ServerFactory):

    def __init__(self, path, chunksize=4096):
        self.chunksize = chunksize
        self.path = path
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, True)
        self.sock.setblocking(0)
        if os.path.exists(self.path):
            os.unlink(self.path)
        self.sock.bind(self.path)
        self.sock.listen(5)
        info("UNIX domain server listening at %s" % path)
    
class UNIXClientSocket:
    """a blocking UNIX domain client socket"""

    def __init__(self, path, chunksize=4096):
        self.chunksize = chunksize
        self.ctl = path
        self.role = 'client'
        self.state = 'up'
        self.txd = ''
        self.rxd = ''
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.setblocking(1)
        info("connecting to %s" % self.ctl)
        self.sock.connect(self.ctl)

    def close(self):
        self.sock.close()

    def read(self,size):
        rxd = ''
        while len(rxd) < size:
            newrxd = self.sock.recv(size - len(rxd))
            if not newrxd:
                return rxd
            rxd += newrxd
        return rxd

    def write(self,txd):
        sent = 0
        while sent < len(txd):
            sent += self.sock.send(txd[sent:])
        return sent

    def shutdown(self):
        self.sock.shutdown(1)

