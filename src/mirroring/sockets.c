// Experimental native-app adapter: delegate a UDP bind on the original socket.
// The descriptor stays owned by Network.framework, preserving its options.
#include <arpa/inet.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

unsigned int mirra_bind_calls;

int mirra_delegated_bind(int fd, const struct sockaddr *address, socklen_t length) {
    const char *path = getenv("MIRRA_BIND_SOCKET");
    int type = 0;
    socklen_t size = sizeof(type);
    if (!path || !address || address->sa_family != AF_INET6 || length != sizeof(struct sockaddr_in6)
            || getsockopt(fd,SOL_SOCKET,SO_TYPE,&type,&size) || type != SOCK_DGRAM)
        return bind(fd,address,length);
    int original=bind(fd,address,length);
    if(original==0 || (errno!=EPERM && errno!=EACCES))return original;
    if (strlen(path) >= sizeof(((struct sockaddr_un *)0)->sun_path)) { errno=ENAMETOOLONG; return -1; }
    int ipc=socket(AF_UNIX,SOCK_STREAM,0);
    if(ipc<0)return -1;
    struct timeval timeout={3,0}; int one=1;
    setsockopt(ipc,SOL_SOCKET,SO_RCVTIMEO,&timeout,sizeof(timeout));
    setsockopt(ipc,SOL_SOCKET,SO_SNDTIMEO,&timeout,sizeof(timeout));
    setsockopt(ipc,SOL_SOCKET,SO_NOSIGPIPE,&one,sizeof(one));
    struct sockaddr_un local={0};local.sun_family=AF_UNIX;local.sun_len=sizeof(local);
    strlcpy(local.sun_path,path,sizeof(local.sun_path));
    uid_t uid;gid_t gid;
    if(connect(ipc,(void*)&local,sizeof(local)) || getpeereid(ipc,&uid,&gid) || uid!=geteuid()) {
        int saved=errno?errno:EACCES;close(ipc);errno=saved;return -1;
    }
    unsigned char payload[8+sizeof(struct sockaddr_in6)]={ 'B','N','D','1' };
    uint32_t encoded=htonl(length);memcpy(payload+4,&encoded,4);memcpy(payload+8,address,length);
    struct iovec iov={payload,sizeof(payload)};
    union {struct cmsghdr align;char bytes[CMSG_SPACE(sizeof(int))];} ancillary={0};
    struct msghdr message={0};message.msg_iov=&iov;message.msg_iovlen=1;
    message.msg_control=ancillary.bytes;message.msg_controllen=sizeof(ancillary.bytes);
    struct cmsghdr *control=CMSG_FIRSTHDR(&message);
    control->cmsg_level=SOL_SOCKET;control->cmsg_type=SCM_RIGHTS;control->cmsg_len=CMSG_LEN(sizeof(int));
    memcpy(CMSG_DATA(control),&fd,sizeof(fd));
    if(sendmsg(ipc,&message,0)!=(ssize_t)sizeof(payload)) {
        int saved=errno?errno:EIO;close(ipc);errno=saved;return -1;
    }
    uint32_t result=0;size_t received=0;
    while(received<sizeof(result)) {
        ssize_t n=recv(ipc,(char*)&result+received,sizeof(result)-received,0);
        if(n<=0){int saved=errno?errno:EIO;close(ipc);errno=saved;return -1;}
        received+=(size_t)n;
    }
    close(ipc);mirra_bind_calls++;
    int error=(int)ntohl(result);
    if(error){errno=error;return -1;}
    return 0;
}

__attribute__((used)) static const struct {const void *replacement;const void *original;}
interpose_bind __attribute__((section("__DATA,__interpose"))) = {
    (const void *)mirra_delegated_bind,(const void *)bind
};
