"""Rank catalogue matches by words, identifier first and description second."""
from functools import lru_cache
import re
import unicodedata


def tokens(text):
    return _tokens(str(text))


@lru_cache(maxsize=32768)
def _tokens(text):
    plain = unicodedata.normalize('NFKD', str(text).casefold())
    return re.findall(r'[a-z]+|[0-9]+', ''.join(c for c in plain if not unicodedata.combining(c)))


def rank_entries(entries, query, disabled=()):
    words = tokens(query)
    if not words:
        return sorted((row for row in entries if row[0].split('/', 1)[0] not in disabled), key=lambda row: row[0])
    ranked = []
    for key, manifest, status in entries:
        bucket, app = key.split('/',1)
        if bucket in disabled:
            continue
        bucket_words, app_words = tokens(bucket), tokens(app)
        description = tokens(manifest.get('description',''))
        extra = tokens(str(manifest.get('version','')) + ' ' + str(manifest.get('notes','')))
        def hits(word, values):
            return any(word == value if word.isdigit() else value.startswith(word) for value in values)
        key_hits = sum(hits(word,bucket_words+app_words) for word in words)
        coverage = sum(hits(word,bucket_words+app_words+description+extra) for word in words)
        if words and not coverage:
            continue
        bucket_hits = sum(hits(word,bucket_words) for word in words)
        exact_app = int(tokens(app) == words)
        score = (-coverage, -key_hits, -bucket_hits, -exact_app, key)
        ranked.append((score,(key,manifest,status)))
    ranked.sort(key=lambda item:item[0])
    return [row for _,row in ranked]
