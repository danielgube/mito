"""Persistent daily update checks, including failed and interrupted attempts."""
from datetime import date
import hashlib
import json
import uuid

from .storage import inside, mutation, read_json, write_json


def daily(root, name, key, operation, *, force=False):
    day = date.today().isoformat()
    identity = hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()
    path = inside(root, 'core/state/' + name + '-check.json')
    token = uuid.uuid4().hex
    with mutation(root, 'update-checks.lock'):
        try:
            previous = read_json(path)
        except (OSError, ValueError):
            previous = {}
        if not force and isinstance(previous, dict) and previous.get('day') == day and previous.get('key') == identity:
            return previous.get('result')
        # Persist before networking: errors/crashes must not retry at every startup.
        record = {'day': day, 'key': identity, 'token': token, 'result': None}
        write_json(path, record)
    result = operation()
    with mutation(root, 'update-checks.lock'):
        if read_json(path).get('token') == token:
            write_json(path, dict(record, result=result))
    return result
