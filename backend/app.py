"""Single-user localhost sheet music library and Smart Pianist handoff service."""
import asyncio
import contextlib
import json
import os
import secrets
import sqlite3
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Literal
from urllib.parse import quote, urljoin, urlparse, urlencode

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel, Field, field_validator

from . import icloud_storage

STATE_DIR = Path(os.environ.get('PIANO_STATE_DIR', Path(__file__).parent / '.state'))
CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID', '')
CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET', '')
REDIRECT_URI = 'http://localhost:8000/api/auth/callback'
FRONTEND = 'http://localhost:5173'
MAX_PDF_BYTES = 25 * 1024 * 1024
ICLOUD_ROOT = Path.home() / 'Library' / 'Mobile Documents' / 'com~apple~CloudDocs'
poll_lock = asyncio.Lock()


def now():
    return datetime.now(timezone.utc).isoformat()


def db():
    STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    connection = sqlite3.connect(STATE_DIR / 'state.sqlite3')
    connection.row_factory = sqlite3.Row
    connection.execute('CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
    connection.execute('CREATE TABLE IF NOT EXISTS queue (id TEXT PRIMARY KEY, drive_id TEXT NOT NULL, title TEXT NOT NULL, status TEXT NOT NULL, web_view_link TEXT NOT NULL, created_at TEXT NOT NULL, folder_id TEXT NOT NULL, UNIQUE(folder_id, drive_id))')
    if 'folder_id' not in [row['name'] for row in connection.execute('PRAGMA table_info(queue)')]:
        # Preserve earlier handoffs while adding folder isolation.
        connection.execute('ALTER TABLE queue RENAME TO queue_legacy')
        connection.execute('CREATE TABLE queue (id TEXT PRIMARY KEY, drive_id TEXT NOT NULL, title TEXT NOT NULL, status TEXT NOT NULL, web_view_link TEXT NOT NULL, created_at TEXT NOT NULL, folder_id TEXT NOT NULL, UNIQUE(folder_id, drive_id))')
        stored = connection.execute("SELECT value FROM kv WHERE key='settings'").fetchone()
        folder = json.loads(stored['value']).get('folder_id', '') if stored else ''
        connection.execute('INSERT INTO queue SELECT id, drive_id, title, status, web_view_link, created_at, ? FROM queue_legacy', (folder,))
        connection.execute('DROP TABLE queue_legacy')
    if 'storage_provider' not in [row['name'] for row in connection.execute('PRAGMA table_info(queue)')]:
        connection.execute("ALTER TABLE queue ADD COLUMN storage_provider TEXT NOT NULL DEFAULT 'google'")
    connection.commit()
    os.chmod(STATE_DIR / 'state.sqlite3', 0o600)
    return connection


def get_value(key, default=None):
    with contextlib.closing(db()) as conn:
        row = conn.execute('SELECT value FROM kv WHERE key=?', (key,)).fetchone()
    return json.loads(row['value']) if row else default


def put_value(key, value):
    with contextlib.closing(db()) as conn:
        conn.execute('INSERT INTO kv VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, json.dumps(value)))
        conn.commit()


class Settings(BaseModel):
    storage_provider: Literal['google', 'icloud'] = 'google'
    icloud_folder: str = Field(default='Piano Uploader', min_length=1, max_length=200)

    @field_validator('icloud_folder')
    @classmethod
    def validate_icloud_folder(cls, value):
        return icloud_storage.validate_folder(value)

    folder_id: str = Field(default='', max_length=200, pattern=r'^[a-zA-Z0-9_-]*$')
    poll_interval_seconds: int = Field(default=60, ge=10, le=86400)
    polling_enabled: bool = False


class Upload(BaseModel):
    pdf_url: str = Field(max_length=2048)
    title: str = Field(min_length=1, max_length=200)
    rights_confirmed: bool = False


def settings():
    return Settings(**get_value('settings', {}))


async def google_token():
    token = get_value('google_token')
    if not token:
        raise HTTPException(401, 'Connect Google Drive in Admin settings first.')
    if token.get('expires_at', 0) > time.time() + 60:
        return token['access_token']
    if not token.get('refresh_token'):
        raise HTTPException(401, 'Google authorization expired. Reconnect Google Drive.')
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post('https://oauth2.googleapis.com/token', data={'client_id': CLIENT_ID, 'client_secret': CLIENT_SECRET, 'grant_type': 'refresh_token', 'refresh_token': token['refresh_token']})
            response.raise_for_status()
            updated = response.json()
        token.update(updated)
        token['expires_at'] = time.time() + updated.get('expires_in', 3600)
        put_value('google_token', token)
        return token['access_token']
    except (httpx.HTTPError, ValueError, KeyError):
        raise HTTPException(401, 'Google authorization could not be refreshed. Reconnect Google Drive.')


async def drive_files():
    cfg = settings()
    if not cfg.folder_id:
        raise HTTPException(400, 'Set a Google Drive folder ID in Admin settings.')
    token = await google_token()
    files = []
    page = None
    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            params = {'q': f"'{cfg.folder_id}' in parents and trashed = false and mimeType = 'application/pdf'", 'fields': 'nextPageToken,files(id,name,mimeType,webViewLink,webContentLink,thumbnailLink,modifiedTime)', 'pageSize': 100, 'orderBy': 'name', 'supportsAllDrives': 'true', 'includeItemsFromAllDrives': 'true'}
            if page:
                params['pageToken'] = page
            try:
                response = await client.get('https://www.googleapis.com/drive/v3/files', params=params, headers={'Authorization': f'Bearer {token}'})
                response.raise_for_status()
                data = response.json()
            except (httpx.HTTPError, ValueError):
                raise HTTPException(502, 'Google Drive listing failed. Check connection and folder permissions.')
            files.extend(data.get('files', []))
            page = data.get('nextPageToken')
            if not page:
                break
    return [{'id': f['id'], 'title': f['name'], 'mime_type': f.get('mimeType', 'application/pdf'), 'web_view_link': f.get('webViewLink', f"https://drive.google.com/file/d/{f['id']}/view"), 'download_url': f.get('webContentLink', ''), 'thumbnail_url': f.get('thumbnailLink', ''), 'modified_time': f.get('modifiedTime', ''), 'storage_provider': 'google', 'folder_hint': cfg.folder_id} for f in files]


def queue_namespace():
    cfg = settings()
    return cfg.folder_id if cfg.storage_provider == 'google' else 'icloud:' + cfg.icloud_folder


async def active_files():
    cfg = settings()
    if cfg.storage_provider == 'icloud':
        return icloud_storage.local_files(ICLOUD_ROOT, cfg.icloud_folder, MAX_PDF_BYTES)
    return await drive_files()


def queue_items():
    with contextlib.closing(db()) as conn:
        rows = [dict(row) for row in conn.execute('SELECT id, drive_id, title, status, web_view_link, created_at, storage_provider FROM queue WHERE folder_id=? ORDER BY created_at DESC', (queue_namespace(),))]
    cfg = settings()
    for row in rows:
        row['folder_hint'] = cfg.icloud_folder if cfg.storage_provider == 'icloud' else cfg.folder_id
    return rows


def enqueue(songs):
    folder_id = queue_namespace()
    provider = settings().storage_provider
    added = 0
    with contextlib.closing(db()) as conn:
        for song in songs:
            item_id = f"{folder_id}:{song['id']}" if folder_id else song['id']
            cursor = conn.execute('INSERT OR IGNORE INTO queue VALUES (?,?,?,?,?,?,?,?)', (item_id, song['id'], song['title'], 'pending', song['web_view_link'], now(), folder_id, provider))
            added += cursor.rowcount
        conn.commit()
    return added


async def poll():
    async with poll_lock:
        songs = await active_files()
        added = enqueue(songs)
        put_value('last_poll_at', now())
        put_value('last_poll_error', None)
        put_value('next_poll_epoch', time.time() + settings().poll_interval_seconds)
        return {'added': added, 'items': queue_items(), 'storage_provider': settings().storage_provider, **({'message': icloud_storage.SCAN_MESSAGE} if settings().storage_provider == 'icloud' else {})}


async def poll_tick():
    cfg = settings()
    if cfg.polling_enabled and (cfg.storage_provider == 'icloud' or (get_value('google_token') and cfg.folder_id)):
        due = get_value('next_poll_epoch', 0)
        if time.time() >= due:
            try:
                await poll()
            except HTTPException as exc:
                put_value('last_poll_error', exc.detail)
                put_value('next_poll_epoch', time.time() + cfg.poll_interval_seconds)
            except Exception:
                put_value('last_poll_error', 'Polling failed. Try Import now to check the connection.')
                put_value('next_poll_epoch', time.time() + cfg.poll_interval_seconds)


async def poll_loop():
    while True:
        await poll_tick()
        await asyncio.sleep(1)


@asynccontextmanager
async def lifespan(app):
    task = asyncio.create_task(poll_loop())
    yield
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


app = FastAPI(title='Piano Uploader', lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=[FRONTEND, 'http://127.0.0.1:5173'], allow_methods=['GET', 'POST', 'PUT'], allow_headers=['Content-Type'])


@app.middleware('http')
async def localhost_only(request: Request, call_next):
    # Protect the local token-bearing service from DNS rebinding and cross-site writes.
    host = request.headers.get('host', '').split(':')[0]
    origin = request.headers.get('origin')
    if host not in {'localhost', '127.0.0.1', 'testserver'} or (origin and origin not in {FRONTEND, 'http://127.0.0.1:5173', 'http://localhost:8000'}):
        return Response('Local requests only', status_code=403)
    return await call_next(request)


@app.get('/api/status')
def status():
    available, message = icloud_storage.availability(ICLOUD_ROOT)
    return {'mode': 'live', 'google_connected': bool(get_value('google_token')), 'oauth_configured': bool(CLIENT_ID and CLIENT_SECRET), 'yamaha_auto_import_supported': False, 'storage_provider': settings().storage_provider, 'icloud_available': available, 'icloud_root_hint': str(ICLOUD_ROOT), 'icloud_message': message}


@app.get('/api/settings')
def read_settings():
    return settings().model_dump()


@app.put('/api/settings')
def save_settings(body: Settings):
    previous = settings()
    if body.storage_provider == 'icloud':
        icloud_storage.folder_path(ICLOUD_ROOT, body.icloud_folder)
    if (previous.storage_provider != body.storage_provider or
            (body.storage_provider == 'google' and previous.folder_id != body.folder_id) or
            (body.storage_provider == 'icloud' and previous.icloud_folder != body.icloud_folder)):
        put_value('last_poll_at', None)
        put_value('last_poll_error', None)
    put_value('settings', body.model_dump())
    put_value('next_poll_epoch', time.time())
    return body.model_dump()


@app.get('/api/auth/start')
def auth_start():
    if not CLIENT_ID or not CLIENT_SECRET:
        raise HTTPException(400, 'Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET on the backend and restart it.')
    state = secrets.token_urlsafe(32)
    put_value('oauth_state', {'value': state, 'expires_at': time.time() + 600})
    query = urlencode({'client_id': CLIENT_ID, 'redirect_uri': REDIRECT_URI, 'response_type': 'code', 'scope': 'https://www.googleapis.com/auth/drive', 'access_type': 'offline', 'prompt': 'consent', 'state': state})
    return {'url': 'https://accounts.google.com/o/oauth2/v2/auth?' + query}


@app.get('/api/auth/callback')
async def auth_callback(state: str = '', code: str = '', error: str = ''):
    expected = get_value('oauth_state', {})
    if not expected or time.time() > expected.get('expires_at', 0) or not secrets.compare_digest(state, expected.get('value', '')):
        raise HTTPException(400, 'Invalid or expired OAuth state. Start the connection again.')
    put_value('oauth_state', None)
    if error or not code:
        return RedirectResponse(FRONTEND + '/?auth=cancelled')
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post('https://oauth2.googleapis.com/token', data={'client_id': CLIENT_ID, 'client_secret': CLIENT_SECRET, 'code': code, 'redirect_uri': REDIRECT_URI, 'grant_type': 'authorization_code'})
            response.raise_for_status()
            token = response.json()
            if not token.get('access_token'):
                raise ValueError('Missing token')
        token['expires_at'] = time.time() + token.get('expires_in', 3600)
        put_value('google_token', token)
        return RedirectResponse(FRONTEND + '/?auth=connected')
    except (httpx.HTTPError, ValueError):
        return RedirectResponse(FRONTEND + '/?auth=failed')


@app.post('/api/auth/disconnect')
async def disconnect():
    token = get_value('google_token')
    put_value('google_token', None)
    put_value('oauth_state', None)
    cfg = settings()
    if cfg.storage_provider == 'google':
        cfg.polling_enabled = False
    put_value('settings', cfg.model_dump())
    revoked = True
    if token:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.post('https://oauth2.googleapis.com/revoke', data={'token': token.get('refresh_token') or token.get('access_token', '')})
                revoked = response.is_success
        except httpx.HTTPError:
            revoked = False
    return {'disconnected': True, 'revoked': revoked}


@app.get('/api/library')
async def library():
    cfg = settings()
    data = {'songs': await active_files(), 'storage_provider': cfg.storage_provider}
    if cfg.storage_provider == 'icloud':
        folder = icloud_storage.folder_path(ICLOUD_ROOT, cfg.icloud_folder)
        placeholders = icloud_storage.placeholder_count(folder)
        data.update(message=icloud_storage.SCAN_MESSAGE + (f' {placeholders} cloud-only placeholder(s) are not downloaded; use Download Now in Finder.' if placeholders else ''), placeholders=placeholders)
    return data


async def search_archive(q: str = ''):
    if not q.strip():
        return {'results': []}
    if len(q) > 200:
        raise HTTPException(400, 'Search must be 200 characters or fewer.')
    escaped = q.replace('\\', '\\\\').replace('"', '\\"')
    try:
        async with httpx.AsyncClient(timeout=25) as client:
            response = await client.get('https://archive.org/advancedsearch.php', params={'q': f'mediatype:texts AND (subject:"sheet music" OR collection:sheetmusic) AND (title:"{escaped}" OR creator:"{escaped}")', 'fl[]': ['identifier', 'title', 'creator'], 'rows': 12, 'output': 'json'})
            response.raise_for_status()
            docs = response.json()['response']['docs']
            gate = asyncio.Semaphore(4)
            async def resolve(doc):
                async with gate:
                    identifier = doc['identifier']
                    metadata_response = await client.get('https://archive.org/metadata/' + quote(identifier, safe=''))
                    metadata_response.raise_for_status()
                    data = metadata_response.json()
                    pdfs = [f for f in data.get('files', []) if f.get('name', '').lower().endswith('.pdf') and f.get('private') not in ['true', True]]
                    if not pdfs:
                        return None
                    pdfs.sort(key=lambda f: (f.get('source') != 'original', '_text.pdf' in f['name']))
                    meta = data.get('metadata', {})
                    creator = doc.get('creator', meta.get('creator', 'Unknown'))
                    license_value = meta.get('licenseurl', '')
                    return {'id': identifier, 'title': doc.get('title', identifier), 'creator': ', '.join(creator) if isinstance(creator, list) else str(creator), 'source': 'Internet Archive', 'page_url': 'https://archive.org/details/' + quote(identifier, safe=''), 'pdf_url': 'https://archive.org/download/' + quote(identifier, safe='') + '/' + quote(pdfs[0]['name'], safe=''), 'thumbnail_url': 'https://archive.org/services/img/' + quote(identifier, safe=''), 'license': ', '.join(license_value) if isinstance(license_value, list) else (license_value or 'Rights not specified — check the source')}
            resolved = await asyncio.gather(*(resolve(doc) for doc in docs), return_exceptions=True)
            results = [result for result in resolved if isinstance(result, dict)]
            errors = sum(isinstance(result, Exception) for result in resolved)
            return {'results': results, **({'error': f'{errors} source records could not be loaded.'} if errors else {})}
    except (httpx.HTTPError, ValueError, KeyError):
        raise HTTPException(502, 'Internet Archive search is unavailable. Try again later.')


def validate_pdf_url(url):
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or '').lower()
        port = parsed.port
    except ValueError:
        raise HTTPException(400, 'Invalid PDF URL.')
    if parsed.scheme != 'https' or parsed.username or parsed.password or port not in (None, 443) or not (host == 'archive.org' or host.endswith('.archive.org')):
        raise HTTPException(400, 'Only HTTPS Internet Archive PDF URLs are supported.')


async def fetch_pdf(url):
    validate_pdf_url(url)
    try:
        async with httpx.AsyncClient(timeout=45, follow_redirects=False) as client:
            for _ in range(6):
                validate_pdf_url(url)
                async with client.stream('GET', url) as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        location = response.headers.get('location')
                        if not location:
                            raise HTTPException(502, 'PDF redirect has no destination.')
                        url = urljoin(url, location)
                        continue
                    response.raise_for_status()
                    length = response.headers.get('content-length', '')
                    if length.isdigit() and int(length) > MAX_PDF_BYTES:
                        raise HTTPException(413, 'PDF exceeds the 25 MB limit.')
                    chunks = []
                    size = 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > MAX_PDF_BYTES:
                            raise HTTPException(413, 'PDF exceeds the 25 MB limit.')
                        chunks.append(chunk)
                    content = b''.join(chunks)
                    if not content.startswith(b'%PDF-'):
                        raise HTTPException(422, 'The source did not return a PDF document.')
                    return content
            raise HTTPException(502, 'Too many PDF redirects.')
    except httpx.HTTPError:
        raise HTTPException(502, 'The PDF source could not be downloaded.')


@app.get('/api/pdf')
async def pdf(url: str):
    content = await fetch_pdf(url)
    return Response(content, media_type='application/pdf', headers={'Content-Disposition': 'inline; filename="sheet-music.pdf"', 'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'private, max-age=300'})


@app.post('/api/upload')
async def upload(body: Upload):
    if not body.rights_confirmed:
        raise HTTPException(400, 'Confirm that you have permission to download and store this sheet music.')
    cfg = settings()
    if cfg.storage_provider == 'icloud':
        icloud_storage.folder_path(ICLOUD_ROOT, cfg.icloud_folder)
        content = await fetch_pdf(body.pdf_url)
        return icloud_storage.save_pdf(ICLOUD_ROOT, cfg.icloud_folder, body.title, content, MAX_PDF_BYTES)
    if not cfg.folder_id:
        raise HTTPException(400, 'Set a Google Drive folder ID first.')
    token = await google_token()
    content = await fetch_pdf(body.pdf_url)
    title = body.title.strip()
    if not title:
        raise HTTPException(400, 'A title is required.')
    if not title.lower().endswith('.pdf'):
        title += '.pdf'
    boundary = secrets.token_hex(20)
    metadata = json.dumps({'name': title, 'mimeType': 'application/pdf', 'parents': [cfg.folder_id]}).encode()
    multipart = b'--' + boundary.encode() + b'\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n' + metadata + b'\r\n--' + boundary.encode() + b'\r\nContent-Type: application/pdf\r\n\r\n' + content + b'\r\n--' + boundary.encode() + b'--\r\n'
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post('https://www.googleapis.com/upload/drive/v3/files', params={'uploadType': 'multipart', 'fields': 'id,name,webViewLink', 'supportsAllDrives': 'true'}, headers={'Authorization': f'Bearer {token}', 'Content-Type': f'multipart/related; boundary={boundary}'}, content=multipart)
            response.raise_for_status()
            result = response.json()
    except (httpx.HTTPError, ValueError):
        raise HTTPException(502, 'Upload to Google Drive failed. Check folder access and try again.')
    return {'id': result['id'], 'title': result.get('name', title), 'storage_provider': 'google', 'web_view_link': result.get('webViewLink', f"https://drive.google.com/file/d/{result['id']}/view")}


@app.get('/api/queue')
def queue():
    epoch = get_value('next_poll_epoch')
    return {'items': queue_items(), 'last_poll_at': get_value('last_poll_at'), 'next_poll_at': datetime.fromtimestamp(epoch, timezone.utc).isoformat() if epoch and settings().polling_enabled else None, 'error': get_value('last_poll_error')}


@app.post('/api/import-now')
async def import_now():
    return await poll()


@app.post('/api/queue/{item_id:path}/opened')
def opened(item_id: str):
    with contextlib.closing(db()) as conn:
        cursor = conn.execute("UPDATE queue SET status='opened' WHERE id=?", (item_id,))
        conn.commit()
        if not cursor.rowcount:
            raise HTTPException(404, 'Queue item not found.')
    return {'id': item_id, 'status': 'opened'}


def image_type(content):
    if content.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if content.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if content.startswith((b'GIF87a', b'GIF89a')):
        return 'image/gif'
    if len(content) >= 12 and content[:4] == b'RIFF' and content[8:12] == b'WEBP':
        return 'image/webp'
    return None


async def fetch_thumbnail(url):
    validate_pdf_url(url)
    if not urlparse(url).path.startswith('/services/img/'):
        raise HTTPException(400, 'Use an Internet Archive thumbnail URL.')
    limit = 2 * 1024 * 1024
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
            for _ in range(6):
                validate_pdf_url(url)
                async with client.stream('GET', url) as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        location = response.headers.get('location')
                        if not location:
                            raise HTTPException(502, 'Thumbnail redirect has no destination.')
                        url = urljoin(url, location)
                        continue
                    response.raise_for_status()
                    declared_type = response.headers.get('content-type', '').split(';')[0].strip().lower()
                    if declared_type not in {'image/jpeg', 'image/png', 'image/gif', 'image/webp'}:
                        raise HTTPException(422, 'The source did not return a supported thumbnail image.')
                    length = response.headers.get('content-length', '')
                    if length.isdigit() and int(length) > limit:
                        raise HTTPException(413, 'Thumbnail exceeds the 2 MB limit.')
                    chunks = []
                    size = 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > limit:
                            raise HTTPException(413, 'Thumbnail exceeds the 2 MB limit.')
                        chunks.append(chunk)
                    content = b''.join(chunks)
                    actual_type = image_type(content)
                    if actual_type != declared_type:
                        raise HTTPException(422, 'The source did not return a valid thumbnail image.')
                    return content, actual_type
            raise HTTPException(502, 'Too many thumbnail redirects.')
    except httpx.HTTPError:
        raise HTTPException(502, 'The thumbnail source could not be loaded.')


@app.get('/api/thumbnail')
async def thumbnail(url: str):
    content, mime_type = await fetch_thumbnail(url)
    return Response(content, media_type=mime_type, headers={'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'private, max-age=3600'})


@app.get('/api/search')
async def search(q: str = '', sources: Optional[str] = None):
    from .search_providers import search_all
    if len(q) > 200:
        raise HTTPException(400, 'Search must be 200 characters or fewer.')
    if not q.strip():
        return {'results': [], 'providers': []}
    return await search_all(q.strip(), sources, search_archive)


@app.get('/api/icloud/files/{identifier}')
def icloud_pdf(identifier: str):
    cfg = settings()
    if cfg.storage_provider != 'icloud':
        raise HTTPException(404, 'Select iCloud storage to view local iCloud PDFs.')
    content = icloud_storage.serve_pdf(ICLOUD_ROOT, cfg.icloud_folder, identifier, MAX_PDF_BYTES)
    return Response(content, media_type='application/pdf', headers={'Content-Disposition': 'inline; filename="sheet-music.pdf"', 'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'no-store'})
