"""BitMidi's public JSON routes, verified against its official open-source site.

Website code licensing does not establish a license for hosted MIDI music.
"""
import re
from urllib.parse import urlencode, urljoin, urlparse

import httpx
from fastapi import HTTPException

from .score_generation import MAX_MIDI_BYTES
from .score_sources import get_bounded

NAME = 'BitMidi'
HOSTS = {'bitmidi.com', 'www.bitmidi.com'}
UNKNOWN_LICENSE = 'Rights not specified — check the BitMidi source and obtain permission to adapt this MIDI'


def search_url(query):
    return 'https://bitmidi.com/search?' + urlencode({'q': query})


def validate_url(url):
    try:
        parsed = urlparse(url)
        valid = parsed.scheme == 'https' and parsed.hostname in HOSTS and parsed.port in (None, 443) and not parsed.username and not parsed.password
    except ValueError:
        valid = False
    if not valid:
        raise HTTPException(400, 'Only HTTPS BitMidi URLs are supported for this source.')
    return parsed


def record(data):
    if not isinstance(data, dict):
        raise ValueError('Invalid BitMidi record.')
    identifier = data.get('id')
    slug = data.get('slug')
    name = data.get('name')
    if type(identifier) is not int or identifier < 1 or not isinstance(slug, str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', slug) or len(slug) > 250 or not isinstance(name, str) or not name.strip() or len(name) > 255:
        raise ValueError('Invalid BitMidi record fields.')
    page = urljoin('https://bitmidi.com/', data.get('url', '/' + slug)) if isinstance(data.get('url', '/' + slug), str) else ''
    download = urljoin('https://bitmidi.com/', data.get('downloadUrl', f'/uploads/{identifier}.mid')) if isinstance(data.get('downloadUrl', f'/uploads/{identifier}.mid'), str) else ''
    page_parts, download_parts = validate_url(page), validate_url(download)
    if page_parts.path != '/' + slug or download_parts.path != f'/uploads/{identifier}.mid' or page_parts.query or page_parts.fragment or download_parts.query or download_parts.fragment:
        raise ValueError('BitMidi links do not match the source record.')
    return {'id': 'bitmidi:' + str(identifier), 'title': name.strip()[:200], 'original_title': name.strip(), 'creator': 'Unknown MIDI arranger (BitMidi)', 'source': NAME, 'page_url': 'https://bitmidi.com/' + slug, 'midi_url': f'https://bitmidi.com/uploads/{identifier}.mid', 'filename': name.strip(), 'pdf_url': '', 'license': UNKNOWN_LICENSE, 'license_url': ''}


async def api(client, route, params):
    response = await get_bounded(client, 'https://bitmidi.com/api/midi/' + route, 1024 * 1024, params=params, validator=validate_url)
    data = response.json()
    envelope = data.get('result') if isinstance(data, dict) else None
    if not isinstance(envelope, dict):
        raise ValueError('The BitMidi public API returned invalid data.')
    return envelope


async def search(query):
    try:
        async with httpx.AsyncClient(timeout=12, follow_redirects=False) as client:
            data = await api(client, 'search', {'q': query, 'page': 0, 'pageSize': 12})
            if not isinstance(data.get('results'), list):
                raise ValueError('The BitMidi public API did not return search records.')
            results = []
            for item in data['results'][:12]:
                try:
                    results.append(record(item))
                except (ValueError, HTTPException):
                    continue
            return {'results': results}
    except (httpx.HTTPError, ValueError, HTTPException):
        raise HTTPException(502, 'BitMidi public MIDI search is unavailable. Open its website or upload an authorized MIDI file.')


async def download_source(url, title, source_page_url=None, cache_dir=None):
    parsed = validate_url(url)
    match = re.fullmatch(r'/uploads/([1-9][0-9]*)\.mid', parsed.path)
    if parsed.query or parsed.fragment:
        raise HTTPException(400, 'Use a direct BitMidi MIDI or source-page URL without extra parameters.')
    if match:
        params = {'id': match.group(1)}
    elif re.fullmatch(r'/[a-z0-9][a-z0-9_-]*', parsed.path) and parsed.path not in {'/about', '/search', '/random', '/privacy'}:
        params = {'slug': parsed.path[1:]}
    else:
        raise HTTPException(400, 'Use a BitMidi source page or /uploads/{id}.mid download URL.')
    if source_page_url:
        validate_url(source_page_url)
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
            data = await api(client, 'get', params)
            item = record(data.get('result'))
            if match and item['midi_url'] != 'https://bitmidi.com' + parsed.path:
                raise HTTPException(422, 'The BitMidi download does not match the requested MIDI.')
            if not match and item['page_url'] != 'https://bitmidi.com' + parsed.path:
                raise HTTPException(422, 'The BitMidi source page does not match its record.')
            if source_page_url:
                source_parts = validate_url(source_page_url)
                if source_parts.path != urlparse(item['page_url']).path or source_parts.query or source_parts.fragment:
                    raise HTTPException(422, 'The selected BitMidi source page does not match this MIDI.')
            from . import midi_cache
            content = midi_cache.read(cache_dir, item['midi_url'])
            cached = content is not None
            if content is None:
                response = await get_bounded(client, item['midi_url'], MAX_MIDI_BYTES, validator=validate_url)
                content = response.content
                if not content.startswith(b'MThd'):
                    raise HTTPException(422, 'The BitMidi source did not return a MIDI document.')
                midi_cache.write(cache_dir, item['midi_url'], content)
            source = {'type': 'bitmidi', 'url': item['midi_url'], 'page_url': item['page_url'], 'title': item['original_title'], 'creator': item['creator'], 'license': item['license'], 'license_url': '', 'pdf_url': '', 'metadata_verified': True}
            source['cached'] = cached
            return content, source
    except (httpx.HTTPError, ValueError):
        raise HTTPException(502, 'The BitMidi MIDI and source record could not be downloaded.')
