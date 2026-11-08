"""USB discovery and connection primitives, independent of a particular model."""
import plistlib
import socket
import struct


def recvn(sock, length):
    result = bytearray()
    while len(result)<length:
        part = sock.recv(length-len(result))
        if not part:
            raise ConnectionError('Device connection closed')
        result.extend(part)
    return bytes(result)


def request(message):
    sock = socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
    sock.settimeout(8)
    try:
        sock.connect('/var/run/usbmuxd')
        payload = plistlib.dumps({'ClientVersionString':'Mirra','ProgName':'Mirra',
                                 'kLibUSBMuxVersion':3,**message})
        sock.sendall(struct.pack('<IIII',16+len(payload),1,8,1)+payload)
        length,version,kind,tag = struct.unpack('<IIII',recvn(sock,16))
        if version!=1 or kind!=8 or tag!=1 or not 16<=length<=1048576:
            raise ConnectionError('Unexpected usbmuxd reply')
        return sock,plistlib.loads(recvn(sock,length-16))
    except BaseException:
        sock.close()
        raise


def connect(device_id, port):
    sock,reply = request({'MessageType':'Connect','DeviceID':device_id,'PortNumber':socket.htons(port)})
    if reply.get('Number')!=0:
        sock.close()
        raise ConnectionError(f'usbmuxd refused device port {port}: {reply.get("Number")}')
    return sock


def product_type(device_id):
    with connect(device_id,62078) as sock:
        payload = plistlib.dumps({'Label':'Mirra','Request':'GetValue','Key':'ProductType'})
        sock.sendall(struct.pack('>I',len(payload))+payload)
        length = struct.unpack('>I',recvn(sock,4))[0]
        if not 0<length<=1048576:
            raise ConnectionError('Unexpected lockdown reply')
        return plistlib.loads(recvn(sock,length)).get('Value')


def list_devices():
    sock,reply = request({'MessageType':'ListDevices'})
    sock.close()
    return [d for d in reply.get('DeviceList',[]) if d.get('Properties',{}).get('ConnectionType')=='USB']


def select_device(devices, *, product='', serial='', product_reader=product_type):
    """Reject missing or ambiguous devices; identifiers are AND filters."""
    if not (product or serial):
        raise ValueError('Select a backend serial or product_type')
    matches = []
    for device in devices:
        properties = device.get('Properties',{})
        if properties.get('ConnectionType')!='USB':
            continue
        if serial and properties.get('SerialNumber')!=serial:
            continue
        if product:
            try:
                if product_reader(device['DeviceID'])!=product:
                    continue
            except (OSError,ConnectionError,ValueError):
                continue
        matches.append(device)
    if len(matches)!=1:
        raise ConnectionError(f'Expected one matching USB backend; found {len(matches)}. Check config/local.json and connected devices')
    return matches[0]
