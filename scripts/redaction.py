"""Remove credential material while retaining the actual diagnostic message."""
import json
import re

REDACTED = '<redacted>'
SENSITIVE_FIELDS = {'password', 'passphrase', 'private_key', 'public_key', 'key',
    'certificate', 'dcrt', 'data', 'ltk', 'local_ltk', 'remote_ltk', 'token',
    '_auth', 'auth_token', 'signature', 'proof', 'pairing_data', 'encrypted_data',
    'payload', 'ciphertext', 'identity', 'serial', 'udid'}
SENSITIVE_NORMALIZED = {re.sub(r'[^a-z0-9]', '', k.lower()) for k in SENSITIVE_FIELDS} | {
    'authtoken', 'pairingtoken', 'localkey', 'remotekey', 'keymaterial', 'devicecertificate',
    'certificatebody', 'signedkey', 'encryptedpayload'}
PEM = re.compile(r'-----BEGIN [^-\r\n]+-----.*?(?:-----END [^-\r\n]+-----|\Z)', re.S)
BLOB = re.compile(r'(?<![A-Za-z0-9+/])(?:[A-Za-z0-9+/]{48,}={0,2}|[0-9a-fA-F]{32,})(?![A-Za-z0-9+/])')
ASSIGNMENT = re.compile(r'(?i)(\b(?:password|passphrase|_auth|auth_token|token|private_key|ltk|local_ltk|remote_ltk|signature|ciphertext)\b\s*[=:]\s*)(?:"[^"\r\n]*"|\'[^\'\r\n]*\'|[^\s,;}]+)')
ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')


def redact_text(text):
    if isinstance(text, bytes):
        try:
            text = text.decode('utf-8')
        except UnicodeError:
            return f'<binary output: {len(text)} bytes>'
    text = str(text)
    text = ANSI.sub('', text)
    text = PEM.sub(REDACTED, text)
    text = ASSIGNMENT.sub(lambda m: m[1] + REDACTED, text)
    text = re.sub(r'\b0x[0-9a-fA-F]{32,}\b', REDACTED, text)
    text = BLOB.sub(REDACTED, text)
    return ''.join(c for c in text if c in '\n\t' or ord(c) >= 32)[:8192]


def redact_value(value, depth=0):
    if depth > 16:
        return '<nested data omitted>'
    if isinstance(value, dict):
        return {str(k): REDACTED if re.sub(r'[^a-z0-9]', '', str(k).lower()) in SENSITIVE_NORMALIZED else redact_value(v, depth + 1)
                for k, v in list(value.items())[:128]}
    if isinstance(value, (list, tuple)):
        return [redact_value(v, depth + 1) for v in value[:128]]
    if isinstance(value, str):
        return redact_text(value)
    if value is None or type(value) in (int, float, bool):
        return value
    return redact_text(value)


def redact_line(raw):
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError, TypeError):
        return redact_text(raw)
    return json.dumps(redact_value(value), ensure_ascii=False, separators=(',', ':'))
