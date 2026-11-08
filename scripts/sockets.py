"""Experimental UDP bind helper for the launched native app.

Authenticate the system app process and act on its transferred socket; do not
create a replacement descriptor. Loopback controls and local AWDL binds are
separate policies; real media acceptance must still be tested.
"""
import array
import ctypes
import errno
import ipaddress
import os
from pathlib import Path
import socket
import struct


class _Sockaddr(ctypes.Structure):
    _fields_=[('length',ctypes.c_ubyte),('family',ctypes.c_ubyte)]


class _Ifaddrs(ctypes.Structure):
    pass


_Ifaddrs._fields_=[('next',ctypes.POINTER(_Ifaddrs)),('name',ctypes.c_char_p),('flags',ctypes.c_uint),
    ('address',ctypes.POINTER(_Sockaddr)),('netmask',ctypes.c_void_p),('destination',ctypes.c_void_p),('data',ctypes.c_void_p)]


def local_awdl_addresses(libc):
    head=ctypes.POINTER(_Ifaddrs)()
    libc.getifaddrs.argtypes=[ctypes.POINTER(ctypes.POINTER(_Ifaddrs))]
    libc.freeifaddrs.argtypes=[ctypes.POINTER(_Ifaddrs)]
    if libc.getifaddrs(ctypes.byref(head)):
        raise OSError(ctypes.get_errno(),'Cannot enumerate AWDL addresses')
    addresses=set()
    try:
        item=head
        while item:
            value=item.contents
            if value.name==b'awdl0' and value.address and value.address.contents.family==socket.AF_INET6 and value.address.contents.length==28:
                raw=ctypes.string_at(value.address,28)
                address=socket.inet_ntop(socket.AF_INET6,raw[8:24])
                if ipaddress.ip_address(address).is_link_local:addresses.add(address)
            item=value.next
    finally:
        libc.freeifaddrs(head)
    return addresses


class BindHelper:
    def __init__(self, expected_executable, *, allow_awdl=False, expected_pid=None):
        self.expected_executable = str(Path(expected_executable).resolve())
        self.libc = ctypes.CDLL(None,use_errno=True)
        self.libc.getpeereid.argtypes = [ctypes.c_int,ctypes.POINTER(ctypes.c_uint),ctypes.POINTER(ctypes.c_uint)]
        self.libc.proc_pidpath.argtypes = [ctypes.c_int,ctypes.c_void_p,ctypes.c_uint]
        self.bound_count = 0
        self.allow_awdl = allow_awdl
        self.expected_pid = expected_pid
        self.last_failure = None

    def authorize(self, connection):
        uid,gid = ctypes.c_uint(),ctypes.c_uint()
        if self.libc.getpeereid(connection.fileno(),ctypes.byref(uid),ctypes.byref(gid)) or uid.value!=os.getuid():
            raise PermissionError('Helper peer has the wrong user')
        pid = struct.unpack('i',connection.getsockopt(0,2,4))[0]  # SOL_LOCAL, LOCAL_PEERPID
        if self.expected_pid is not None and pid!=self.expected_pid:
            raise PermissionError('Helper peer is not the launched app process')
        path = ctypes.create_string_buffer(4096)
        if self.libc.proc_pidpath(pid,path,len(path))<=0 or os.fsdecode(path.value)!=self.expected_executable:
            raise PermissionError('Helper peer is not the expected system app')

    def handle(self, connection):
        descriptors=[]
        error=errno.EINVAL
        connection.settimeout(3)
        try:
            self.authorize(connection)
            body,control,flags,_ = connection.recvmsg(36,socket.CMSG_SPACE(8*4))
            for level,kind,data in control:
                if level==socket.SOL_SOCKET and kind==socket.SCM_RIGHTS:
                    values=array.array('i');values.frombytes(data[:len(data)-(len(data)%values.itemsize)])
                    descriptors.extend(values)
            if flags & (socket.MSG_TRUNC|socket.MSG_CTRUNC) or len(descriptors)!=1:
                raise ValueError('Expected exactly one socket descriptor')
            while len(body)<36:
                piece=connection.recv(36-len(body))
                if not piece:raise ValueError('Incomplete request')
                body+=piece
            if body[:4]!=b'BND1' or struct.unpack('!I',body[4:8])[0]!=28:
                raise ValueError('Invalid bind protocol')
            addr=body[8:]
            if addr[0]!=28 or addr[1]!=socket.AF_INET6:
                raise ValueError('Expected a Darwin IPv6 address')
            port=struct.unpack('!H',addr[2:4])[0]
            address=socket.inet_ntop(socket.AF_INET6,addr[8:24])
            scope=struct.unpack('=I',addr[24:28])[0]
            fd=descriptors[0]
            udp=socket.socket(fileno=fd)
            descriptors.pop()
            with udp:
                if udp.family!=socket.AF_INET6 or udp.getsockopt(socket.SOL_SOCKET,socket.SO_TYPE)!=socket.SOCK_DGRAM:
                    raise ValueError('Expected an IPv6 UDP socket')
                if udp.getsockname()[1]!=0:
                    raise ValueError('Socket is already bound')
                if port and port<1024:
                    raise PermissionError('Privileged ports are outside this helper policy')
                if address!='::1' or scope!=0:
                    if not self.allow_awdl:
                        raise PermissionError('This probe permits loopback only')
                    interface=socket.if_nametoindex('awdl0')
                    local=local_awdl_addresses(self.libc)
                    bound_interface=udp.getsockopt(socket.IPPROTO_IPV6,125)  # IPV6_BOUND_IF
                    if address=='::' and scope==0 and bound_interface==interface:
                        # Constrain an interface-bound wildcard reservation to
                        # this host's actual AWDL address, retaining its port.
                        if not local:raise PermissionError('No local AWDL address')
                        address=sorted(local)[0];scope=interface
                    if address not in local or scope not in (0,interface) or bound_interface not in (0,interface):
                        raise PermissionError('Socket is not a local AWDL reservation')
                    scope=interface
                udp.bind((address,port,0,scope))
            self.bound_count+=1
            error=0
        except PermissionError as problem:
            self.last_failure=str(problem)
            error=errno.EACCES
        except OSError as problem:
            self.last_failure=str(problem)
            error=problem.errno or errno.EIO
        except ValueError as problem:
            self.last_failure=str(problem)
            error=errno.EINVAL
        finally:
            for fd in descriptors:os.close(fd)
        connection.sendall(struct.pack('!I',error))
        return error
