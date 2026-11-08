#!/usr/bin/env python3
"""Explain the installed service's current state without changing it."""
import argparse
import json
from readiness import observe


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', action='store_true', help='Return selected status fields for the support window')
    args = parser.parse_args()
    value = observe()
    if args.json:
        print(json.dumps(value, indent=2))
    else:
        print(value['title'] + '\n\n' + value['detail'])
        if value['bridge_transport']:
            print('\nBridge connection: ' + ('Wi-Fi / SSH' if value['bridge_transport'] == 'ssh' else 'USB'))
        print('\nThis checks support readiness. Live video, input and audio still need a real Mirroring session.')


if __name__ == '__main__':
    main()
