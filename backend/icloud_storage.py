"""Local macOS iCloud Drive files; Apple owns account setup and synchronization."""
import hashlib
import os
import platform
import re
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException

SYNC_MESSAGE = 'Saved locally in iCloud Drive. Apple sync to your iPad is not verified; check Files → iCloud Drive before importing into Smart Pianist.'
SCAN_MESSAGE = 'Scanned local iCloud Drive PDFs. Apple sync and Smart Pianist import are not verified.'


def validate_folder(value):
    if not value or len(value) > 200 or '\\' in value or ':' in value or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError('Use a relative iCloud Drive subfolder without control characters or path separators from other platforms.')
    path = Path(value)
    if path.is_absolute() or any(part in {'', '.', '..'} for part in value.split('/')):
        raise ValueError('Use a relative iCloud Drive subfolder without . or .. components.')
    return value


def availability(root):
    if platform.system() != 'Darwin':
        return False, 'iCloud Drive storage requires this backend to run locally on your Mac. Configure your Apple Account and iCloud Drive in macOS System Settings.'
    if not root.is_dir() or root.is_symlink() or not os.access(root, os.R_OK | os.W_OK | os.X_OK):
        return False, 'The local iCloud Drive folder is unavailable. Enable iCloud Drive in macOS System Settings and allow this app to access its folder.'
    return True, 'Local iCloud Drive is accessible. This does not verify Apple sync or availability on your iPad.'


def folder_path(root, relative, create=False):
    validate_folder(relative)
    available, message = availability(root)
    if not available:
        raise HTTPException(422, message)
    root_real = root.resolve()
    folder = root
    try:
        for component in relative.split('/'):
            folder = folder / component
            if folder.is_symlink():
                raise HTTPException(422, 'iCloud subfolder symlinks are not supported.')
            if create:
                folder.mkdir(exist_ok=True)
            if folder.exists() and not folder.is_dir():
                raise HTTPException(422, 'The iCloud subfolder path is not a directory.')
            if not folder.resolve().is_relative_to(root_real):
                raise HTTPException(422, 'The iCloud subfolder must stay inside iCloud Drive.')
    except OSError:
        raise HTTPException(422, 'The iCloud subfolder could not be accessed. Check folder permissions.')
    return folder


def file_id(name):
    return hashlib.sha256(name.encode('utf-8')).hexdigest()[:32]


def read_pdf(path, max_bytes, header_only=False):
    try:
        before = path.lstat()
        if getattr(before, 'st_flags', 0) & getattr(stat, 'SF_DATALESS', 0x40000000):
            raise HTTPException(409, 'This iCloud PDF is cloud-only. Download it in Finder first.')
        descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(descriptor, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > max_bytes:
                raise HTTPException(413, 'Local PDF exceeds the 25 MB limit or is not a regular file.')
            content = stream.read(5 if header_only else max_bytes + 1)
            if len(content) > max_bytes:
                raise HTTPException(413, 'Local PDF exceeds the 25 MB limit.')
            if not content.startswith(b'%PDF-'):
                raise HTTPException(422, 'The local file is not a downloaded PDF document.')
            return content, info
    except OSError:
        raise HTTPException(404, 'The local PDF could not be opened. Download it from iCloud Drive in Finder first.')


def local_files(root, relative, max_bytes):
    folder = folder_path(root, relative)
    if not folder.exists():
        return []
    songs = []
    try:
        entries = sorted(folder.iterdir(), key=lambda path: path.name.lower())
    except OSError:
        raise HTTPException(422, 'The iCloud subfolder could not be listed.')
    for path in entries:
        if path.suffix.lower() != '.pdf' or path.is_symlink() or not path.is_file():
            continue
        try:
            # .icloud placeholders and unavailable PDFs are excluded, never called synced.
            _, info = read_pdf(path, max_bytes, header_only=True)
        except HTTPException:
            continue
        identifier = file_id(path.name)
        link = '/api/icloud/files/' + identifier
        songs.append({'id': identifier, 'title': path.name, 'mime_type': 'application/pdf', 'web_view_link': link, 'download_url': link, 'thumbnail_url': '', 'modified_time': datetime.fromtimestamp(info.st_mtime, timezone.utc).isoformat(), 'storage_provider': 'icloud', 'folder_hint': relative})
    return songs


def filename(title):
    cleaned = re.sub(r'[\\/:\x00-\x1f\x7f]', '_', title).strip().strip('.')[:180].strip()
    if cleaned.lower().endswith('.pdf'):
        cleaned = cleaned[:-4].rstrip(' .')
    return (cleaned or 'Sheet music') + '.pdf'


def save_pdf(root, relative, title, content, max_bytes):
    if len(content) > max_bytes:
        raise HTTPException(413, 'PDF exceeds the 25 MB limit.')
    if not content.startswith(b'%PDF-'):
        raise HTTPException(422, 'The source did not return a PDF document.')
    folder = folder_path(root, relative, create=True)
    name = filename(title)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=folder, prefix='.piano-', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        for index in range(1, 1001):
            candidate = folder / (name if index == 1 else f'{name[:-4]} ({index}).pdf')
            try:
                # Reserve a new destination without replacing existing files. Publish
                # complete contents with an atomic rename supported by FileProvider.
                reservation = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                reserved = os.fstat(reservation)
                os.close(reservation)
                try:
                    current = candidate.lstat()
                    if current.st_ino != reserved.st_ino or current.st_dev != reserved.st_dev:
                        raise HTTPException(409, 'The destination changed during save. Choose another title.')
                    os.replace(temporary, candidate)
                    temporary = None
                except Exception:
                    if candidate.exists():
                        current = candidate.lstat()
                        if current.st_ino == reserved.st_ino and current.st_dev == reserved.st_dev:
                            candidate.unlink()
                    raise
                identifier = file_id(candidate.name)
                link = '/api/icloud/files/' + identifier
                return {'id': identifier, 'title': candidate.name, 'storage_provider': 'icloud', 'web_view_link': link, 'download_url': link, 'folder_hint': relative, 'message': SYNC_MESSAGE}
            except FileExistsError:
                continue
        raise HTTPException(409, 'Too many files with this title. Choose a different title.')
    except OSError:
        raise HTTPException(422, 'Could not save the local iCloud PDF. Check folder permissions and disk space.')
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def serve_pdf(root, relative, identifier, max_bytes):
    if not re.fullmatch(r'[a-f0-9]{32}', identifier):
        raise HTTPException(404, 'Local PDF not found.')
    folder = folder_path(root, relative)
    if folder.exists():
        for path in folder.iterdir():
            if path.suffix.lower() == '.pdf' and not path.is_symlink() and file_id(path.name) == identifier:
                return read_pdf(path, max_bytes)[0]
    raise HTTPException(404, 'Local PDF not found in the selected iCloud subfolder.')


def placeholder_count(folder):
    if not folder.exists():
        return 0
    count = 0
    try:
        for path in folder.iterdir():
            if path.is_symlink():
                continue
            info = path.lstat()
            if path.name.lower().endswith('.icloud') or (path.suffix.lower() == '.pdf' and getattr(info, 'st_flags', 0) & getattr(stat, 'SF_DATALESS', 0x40000000)):
                count += 1
    except OSError:
        raise HTTPException(422, 'The iCloud subfolder could not be inspected.')
    return count
