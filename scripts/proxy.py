#!/usr/bin/env python3
"""Forward a loopback TCP port to the configured USB backend."""
import argparse
from dataclasses import replace
import signal
import threading
from configuration import ConfigError,load_config
from forwarder import USBForwarder


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',help='Configuration JSON (defaults to config/local.json)')
    parser.add_argument('--product',help='Optional Apple product identifier filter')
    parser.add_argument('--serial',help='Stable USB device identifier; disambiguates identical models')
    parser.add_argument('--listen-port',type=int)
    parser.add_argument('--device-port',type=int)
    args = parser.parse_args()
    try:
        config = load_config(args.config)
        for name,value in [('product_type',args.product),('serial',args.serial),
                           ('listen_port',args.listen_port),('device_port',args.device_port)]:
            if value is not None:
                config = replace(config,**{name:value})
        if config.transport!='usb':
            parser.error('The configured backend uses SSH directly; no USB proxy is required')
        if not 1<=config.listen_port<=65535 or not 1<=config.device_port<=65535:
            parser.error('Ports must be in 1..65535')
        proxy = USBForwarder(config)
        proxy.device_id()
    except (ConfigError,OSError,ConnectionError,ValueError) as error:
        parser.error(str(error))

    stop = threading.Event()
    signal.signal(signal.SIGTERM,lambda *_:stop.set())
    signal.signal(signal.SIGINT,lambda *_:stop.set())
    try:
        proxy.start()
        print(f'USB backend: 127.0.0.1:{config.listen_port} -> device:{config.device_port}',flush=True)
        while not stop.wait(1):
            pass
    finally:
        proxy.close()


if __name__=='__main__':
    main()
