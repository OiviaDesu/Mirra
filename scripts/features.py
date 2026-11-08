#!/usr/bin/env python3
"""Inspect native feature settings and catalog without requiring a backend profile."""
import argparse
import json
from capabilities import inspect_native_features


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', action='store_true', help='Print selected capability fields for sharing')
    args = parser.parse_args()
    report = inspect_native_features()
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        notifications = report['notifications']
        apps = report['phone_apps']
        print('Native notification setting: ' + notifications['setting'])
        print(notifications['next_check'])
        print('Phone-app catalog: ' + apps['catalog'] +
              (f' ({apps["count"]} entries)' if 'count' in apps else ''))
        print(apps['next_check'])
        for part in (notifications, apps):
            if 'query_error' in part:
                print(part['query_error'])
        print('Notification delivery, phone-app launching and daily use remain unverified by this check.')
    # Success means inspection completed, never that native features work.
    return int(report['notifications']['setting'] == 'unknown' or report['phone_apps']['catalog'] == 'unknown')


if __name__ == '__main__':
    raise SystemExit(main())
