"""Public MIDI discovery and bounded downloads, restricted to Internet Archive."""
import asyncio
from pathlib import PurePosixPath
from urllib.parse import quote, unquote, urljoin, urlparse

import httpx
from fastapi import HTTPException

from .score_generation import MAX_MIDI_BYTES


def validate_url(url):
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or '').lower()
        valid = parsed.scheme == 'https' and (host == 'archive.org' or host.endswith('.archive.org')) and parsed.port in (None, 443) and not parsed.username and not parsed.password
    except ValueError:
        valid = False
    if not valid:
        raise HTTPException(400, 'Only HTTPS Internet Archive MIDI URLs are supported.')


async def get_bounded(client, url, limit, params=None):
    for _ in range(6):
        validate_url(url)
        async with client.stream('GET', url, params=params) as response:
            params = None
            if response.status_code in (301, 302, 303, 307, 308):
                if not response.headers.get('location'):
                    raise HTTPException(502, 'The source redirect has no destination.')
                url = urljoin(str(response.url), response.headers['location'])
                continue
            response.raise_for_status()
            length = response.headers.get('content-length', '')
            if length.isdigit() and int(length) > limit:
                raise HTTPException(413, 'The source exceeds the supported size limit.')
            chunks = []
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, 'The source exceeds the supported size limit.')
                chunks.append(chunk)
            headers = dict(response.headers)
            headers.pop('content-encoding', None)
            return httpx.Response(response.status_code, content=b''.join(chunks), headers=headers, request=response.request)
    raise HTTPException(502, 'Too many source redirects.')


def text(value, default=''):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return ', '.join(v for v in value if isinstance(v, str))
    return default


def source_metadata(identifier, metadata, filename, url, title):
    meta = metadata.get('metadata', {}) if isinstance(metadata, dict) else {}
    meta = meta if isinstance(meta, dict) else {}
    files = metadata.get('files', []) if isinstance(metadata, dict) else []
    files = files if isinstance(files, list) else []
    stem = str(PurePosixPath(filename).with_suffix(''))
    matching_pdf = next((f['name'] for f in files if isinstance(f, dict) and isinstance(f.get('name'), str) and f['name'].lower().endswith('.pdf') and str(PurePosixPath(f['name']).with_suffix('')) == stem and f.get('private') not in (True, 'true')), '')
    license_url = text(meta.get('licenseurl'))
    return {'type': 'archive', 'url': url, 'page_url': 'https://archive.org/details/' + quote(identifier, safe=''), 'title': text(meta.get('title'), title) or title, 'creator': text(meta.get('creator'), 'Unknown source author') or 'Unknown source author', 'license': license_url or 'Rights not specified — check the source', 'license_url': license_url, 'pdf_url': ('https://archive.org/download/' + quote(identifier, safe='') + '/' + quote(matching_pdf, safe='')) if matching_pdf else ''}


async def download_source(url, title):
    validate_url(url)
    path = urlparse(url).path
    parts = path.split('/')
    if len(parts) < 4 or parts[1] != 'download' or not unquote(path).lower().endswith(('.mid', '.midi')):
        raise HTTPException(400, 'Use a direct Internet Archive .mid or .midi download URL.')
    identifier = unquote(parts[2])
    filename = unquote('/'.join(parts[3:]))
    try:
        async with httpx.AsyncClient(timeout=25, follow_redirects=False) as client:
            response = await get_bounded(client, url, MAX_MIDI_BYTES)
            content = response.content
            if not content.startswith(b'MThd'):
                raise HTTPException(422, 'The source did not return a MIDI document.')
            try:
                metadata_response = await get_bounded(client, 'https://archive.org/metadata/' + quote(identifier, safe=''), 2 * 1024 * 1024)
                metadata = metadata_response.json()
                if not isinstance(metadata, dict):
                    metadata = {}
            except (httpx.HTTPError, HTTPException, ValueError):
                metadata = {}
            source = source_metadata(identifier, metadata, filename, url, title)
            source['metadata_verified'] = bool(metadata.get('metadata'))
            return content, source
    except httpx.HTTPError:
        raise HTTPException(502, 'The MIDI source could not be downloaded.')


async def search_midi(query):
    escaped = query.replace('\\', '\\\\').replace('"', '\\"')
    try:
        async with httpx.AsyncClient(timeout=12, follow_redirects=False) as client:
            response = await get_bounded(client, 'https://archive.org/advancedsearch.php', 1024 * 1024, {'q': f'title:"{escaped}" AND format:MIDI', 'fl[]': ['identifier', 'title', 'creator'], 'rows': 12, 'output': 'json'})
            data = response.json()
            search_data = data.get('response') if isinstance(data, dict) else None
            if not isinstance(search_data, dict) or not isinstance(search_data.get('docs'), list):
                raise ValueError('Invalid search data')
            gate = asyncio.Semaphore(4)
            async def resolve(doc):
                if not isinstance(doc, dict) or not isinstance(doc.get('identifier'), str):
                    return []
                identifier = doc['identifier']
                async with gate:
                    response = await get_bounded(client, 'https://archive.org/metadata/' + quote(identifier, safe=''), 2 * 1024 * 1024)
                    data = response.json()
                    if not isinstance(data, dict) or not isinstance(data.get('files'), list):
                        raise ValueError('Invalid metadata')
                    midi_files = [f for f in data['files'] if isinstance(f, dict) and isinstance(f.get('name'), str) and f['name'].lower().endswith(('.mid', '.midi')) and f.get('private') not in (True, 'true')]
                    midi_files = [f for f in midi_files if not str(f.get('size', '')).isdigit() or int(f['size']) <= MAX_MIDI_BYTES]
                    midi_files.sort(key=lambda f: (f.get('source') != 'original', f['name']))
                    records = []
                    for file in midi_files[:2]:
                        filename = file['name']
                        url = 'https://archive.org/download/' + quote(identifier, safe='') + '/' + quote(filename, safe='')
                        title = text(doc.get('title'), identifier) or identifier
                        source = source_metadata(identifier, data, filename, url, title)
                        records.append({'id': identifier + ':' + filename, 'title': source['title'], 'creator': source['creator'], 'source': 'Internet Archive', 'page_url': source['page_url'], 'midi_url': url, 'filename': filename, 'pdf_url': source['pdf_url'], 'license': source['license'], 'license_url': source['license_url']})
                    return records
            resolved = await asyncio.gather(*(resolve(doc) for doc in search_data['docs'][:12]), return_exceptions=True)
            errors = sum(isinstance(result, Exception) for result in resolved)
            results = [item for group in resolved if isinstance(group, list) for item in group][:12]
            return {'results': results, **({'error': f'{errors} source records could not be loaded.'} if errors else {})}
    except (httpx.HTTPError, ValueError, HTTPException):
        raise HTTPException(502, 'Public MIDI search is unavailable. Try sheet-music search or upload an authorized MIDI file.')
