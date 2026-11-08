#import <Foundation/Foundation.h>
#import <Network/Network.h>
#include <arpa/inet.h>
#include <errno.h>
#include <mach-o/dyld.h>
#include <sys/socket.h>
#include <unistd.h>
extern int mirra_delegated_bind(int,const struct sockaddr*,socklen_t);
extern unsigned int mirra_bind_calls;

__attribute__((constructor)) static void probe(void) {
    const char *mode=getenv("MIRRA_BIND_PROBE");
    if(!mode || strcmp(mode,"exit-before-main-v1"))return;
    char executable[4096];uint32_t length=sizeof(executable);
    const char *expected=getenv("MIRRA_EXPECTED_EXECUTABLE");
    if(!expected || _NSGetExecutablePath(executable,&length) || strcmp(executable,expected))_exit(90);
    @autoreleasepool {
        struct sockaddr_in6 local={0};local.sin6_len=sizeof(local);local.sin6_family=AF_INET6;
        inet_pton(AF_INET6,"::1",&local.sin6_addr);
        int direct=socket(AF_INET6,SOCK_DGRAM,0);errno=0;
        int status=bind(direct,(void*)&local,sizeof(local));int originalError=errno;close(direct);
        printf("{\"stage\":\"original_bind\",\"status\":%d,\"errno\":%d}\n",status,originalError);
        int fd=socket(AF_INET6,SOCK_DGRAM,0);errno=0;
        int delegated=mirra_delegated_bind(fd,(void*)&local,sizeof(local));int bindError=errno;
        struct sockaddr_in6 peer=local;
        peer.sin6_port=htons((uint16_t)strtoul(getenv("MIRRA_BIND_PEER_PORT"),NULL,10));
        struct timeval timeout={3,0};setsockopt(fd,SOL_SOCKET,SO_RCVTIMEO,&timeout,sizeof(timeout));
        int connected=delegated==0?connect(fd,(void*)&peer,sizeof(peer)):-1;
        ssize_t sent=connected==0?send(fd,"bind-probe",10,0):-1;
        char ack[16]={0};ssize_t received=sent==10?recv(fd,ack,sizeof(ack),0):-1;close(fd);
        BOOL roundtrip=received==6&&!memcmp(ack,"ack-v1",6);
        printf("{\"stage\":\"delegated_socket\",\"bind\":%d,\"errno\":%d,\"connected\":%d,\"roundtrip\":%s}\n",delegated,bindError,connected,roundtrip?"true":"false");
        unsigned int before=mirra_bind_calls;
        nw_parameters_t parameters=nw_parameters_create_secure_udp(NW_PARAMETERS_DISABLE_PROTOCOL,NW_PARAMETERS_DEFAULT_CONFIGURATION);
        nw_parameters_set_local_endpoint(parameters,nw_endpoint_create_address((void*)&local));
        nw_listener_t listener=nw_listener_create(parameters);
        dispatch_semaphore_t done=dispatch_semaphore_create(0);
        __block BOOL ready=NO;__block int failure=0;
        if(listener){
            nw_listener_set_queue(listener,dispatch_get_global_queue(QOS_CLASS_USER_INITIATED,0));
            nw_listener_set_state_changed_handler(listener,^(nw_listener_state_t state,nw_error_t error){
                if(state==nw_listener_state_ready){ready=YES;dispatch_semaphore_signal(done);}
                else if(state==nw_listener_state_failed){failure=error?nw_error_get_error_code(error):-1;dispatch_semaphore_signal(done);}
            });
            nw_listener_set_new_connection_handler(listener,^(nw_connection_t connection){nw_connection_cancel(connection);});
            nw_listener_start(listener);
            dispatch_semaphore_wait(done,dispatch_time(DISPATCH_TIME_NOW,5*NSEC_PER_SEC));
        }
        printf("{\"stage\":\"network_listener\",\"ready\":%s,\"error\":%d,\"delegated_bind_calls\":%u}\n",ready?"true":"false",failure,mirra_bind_calls-before);
        if(listener)nw_listener_cancel(listener);
        fflush(stdout);_exit(roundtrip&&ready?0:1);
    }
}
