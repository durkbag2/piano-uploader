"""Small private, expiring cache of validated source download bytes."""
import hashlib
import os
import re
import tempfile
import time

from .score_generation import MAX_MIDI_BYTES

TTL_SECONDS = 24 * 60 * 60
MAX_ENTRIES = 20
MAGIC = b'PMC1'


def cache_path(root, url):
    return root / (hashlib.sha256(url.encode('utf-8')).hexdigest() + '.cached')


def read(root, url):
    if root is None or root.is_symlink():
        return None
    path = cache_path(root, url)
    try:
        if path.is_symlink() or time.time() - path.stat().st_mtime > TTL_SECONDS:
            return None
        with path.open('rb') as stream:
            data = stream.read(MAX_MIDI_BYTES + 37)
        content = data[36:]
        if len(data) > MAX_MIDI_BYTES + 36 or data[:4] != MAGIC or not content.startswith(b'MThd') or data[4:36] != hashlib.sha256(content).digest():
            return None
        return content
    except OSError:
        return None


def write(root, url, content):
    if root is None or len(content) > MAX_MIDI_BYTES or not content.startswith(b'MThd'):
        return
    temporary = None
    try:
        if root.is_symlink():
            return
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        root.chmod(0o700)
        path = cache_path(root, url)
        if path.is_symlink():
            return
        with tempfile.NamedTemporaryFile(dir=root, prefix='.midi-', delete=False) as stream:
            temporary = stream.name
            stream.write(MAGIC + hashlib.sha256(content).digest() + content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
        entries = [p for p in root.iterdir() if re.fullmatch(r'[a-f0-9]{64}\.cached', p.name) and not p.is_symlink() and p.is_file()]
        entries.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        for entry in entries[MAX_ENTRIES:]:
            entry.unlink()
    except OSError:
        # Caching is optional; an unwritable cache must not prevent generation.
        pass
    finally:
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass
