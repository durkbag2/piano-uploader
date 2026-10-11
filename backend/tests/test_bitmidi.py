"""Fixtures follow feross/bitmidi.com public server/API/model definitions.
They verify the documented adapter contract, not current live-site access.
"""
import asyncio
import io
from urllib.parse import parse_qs, urlparse

import httpx
import mido
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend import app as service
from backend import bitmidi
from backend import score_sources

RECORD = {'id': 123, 'name': 'Moonlight-Sonata.mid', 'slug': 'moonlight-sonata-mid', 'url': '/moonlight-sonata-mid', 'downloadUrl': '/uploads/123.mid'}


def fixture_midi():
    midi = mido.MidiFile()
    track = mido.MidiTrack()
    track.append(mido.Message('note_on', note=60, velocity=80))
    track.append(mido.Message('note_off', note=60, velocity=0, time=480))
    midi.tracks.append(track)
    stream = io.BytesIO()
    midi.save(file=stream)
    return stream.getvalue()


def mock_http(monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr(bitmidi.httpx, 'AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(service, 'STATE_DIR', tmp_path / 'state')
    return TestClient(service.app)


def test_official_bitmidi_search_envelope_returns_real_record_and_encoded_query(monkeypatch):
    query = 'Moonlight & Für Elise + Piano'
    def respond(request):
        assert request.url.path == '/api/midi/search'
        assert request.url.params['q'] == query
        assert request.url.params['pageSize'] == '12'
        assert 'authorization' not in request.headers
        return httpx.Response(200, json={'result': {'query': {'q': query}, 'results': [RECORD], 'total': 1, 'pageTotal': 1}})
    mock_http(monkeypatch, respond)
    results = asyncio.run(bitmidi.search(query))['results']
    assert len(results) == 1
    assert results[0]['midi_url'] == 'https://bitmidi.com/uploads/123.mid'
    assert results[0]['page_url'] == 'https://bitmidi.com/moonlight-sonata-mid'
    assert 'Rights not specified' in results[0]['license']
    assert results[0]['license_url'] == ''
    assert parse_qs(urlparse(bitmidi.search_url(query)).query)['q'] == [query]


@pytest.mark.parametrize('change', [
    {'url': 'https://evil.example/moonlight-sonata-mid'}, {'downloadUrl': 'https://archive.org/download/x/x.mid'},
    {'downloadUrl': '/uploads/999.mid'}, {'downloadUrl': '/uploads/123.mid?redirect=evil'},
    {'url': '/search'}, {'id': True}, {'id': '123'}, {'slug': '../outside'},
    {'downloadUrl': 'https://bitmidi.com:8443/uploads/123.mid'},
])
def test_record_rejects_malicious_or_mismatched_links(change):
    with pytest.raises((ValueError, HTTPException)):
        bitmidi.record({**RECORD, **change})


def test_search_failures_do_not_drop_working_archive_results(monkeypatch):
    async def archive(query):
        return {'results': [{'id': 'real', 'title': 'Archive MIDI'}]}
    async def broken(query):
        raise HTTPException(502, 'BitMidi unreachable')
    monkeypatch.setattr(score_sources, 'search_midi', archive)
    monkeypatch.setattr(bitmidi, 'search', broken)
    result = asyncio.run(score_sources.search_all_midi('moonlight'))
    assert len(result['results']) == 1
    assert [provider['id'] for provider in result['providers']] == ['bitmidi', 'archive']
    assert [provider['status'] for provider in result['providers']] == ['unavailable', 'ok']
    assert result['providers'][0]['search_url'] == 'https://bitmidi.com/search?q=moonlight'


def test_sources_selection_and_unknown_provider_validation(monkeypatch):
    calls = []
    async def search(query):
        calls.append(query)
        return {'results': []}
    monkeypatch.setattr(bitmidi, 'search', search)
    result = asyncio.run(score_sources.search_all_midi('song', 'bitmidi,bitmidi'))
    assert calls == ['song']
    assert [p['id'] for p in result['providers']] == ['bitmidi']
    with pytest.raises(HTTPException) as error:
        asyncio.run(score_sources.search_all_midi('song', 'wrong'))
    assert error.value.status_code == 400


def test_create_from_bitmidi_validates_source_record_and_preserves_unknown_rights(client, monkeypatch):
    content = fixture_midi()
    calls = []
    def respond(request):
        calls.append(request)
        assert request.url.host == 'bitmidi.com'
        assert 'authorization' not in request.headers
        if request.url.path == '/api/midi/get':
            assert request.url.params['id'] == '123'
            return httpx.Response(200, json={'result': {'query': {'id': '123'}, 'result': RECORD, 'related': []}})
        assert request.url.path == '/uploads/123.mid'
        return httpx.Response(200, content=content)
    mock_http(monkeypatch, respond)
    response = client.post('/api/scores/from-source', json={'midi_url': 'https://bitmidi.com/uploads/123.mid', 'source_page_url': 'https://bitmidi.com/moonlight-sonata-mid', 'title': 'Moonlight draft', 'rights_confirmed': True})
    assert response.status_code == 200
    result = response.json()
    assert result['source']['type'] == 'bitmidi'
    assert result['source']['page_url'] == 'https://bitmidi.com/moonlight-sonata-mid'
    assert 'Rights not specified' in result['source']['license']
    assert 'Unknown MIDI arranger' in result['musicxml']
    assert result['note_count'] == 1
    assert len(calls) == 2


def test_bitmidi_source_page_can_resolve_official_download(monkeypatch):
    def respond(request):
        if request.url.path == '/api/midi/get':
            assert request.url.params['slug'] == RECORD['slug']
            return httpx.Response(200, json={'result': {'result': RECORD}})
        return httpx.Response(200, content=fixture_midi())
    mock_http(monkeypatch, respond)
    data, source = asyncio.run(score_sources.download_source('https://bitmidi.com/moonlight-sonata-mid', 'Draft'))
    assert data.startswith(b'MThd')
    assert source['url'] == 'https://bitmidi.com/uploads/123.mid'


@pytest.mark.parametrize('destination', ['https://archive.org/download/id/file.mid', 'https://127.0.0.1/private', 'https://bitmidi.com:bad/private'])
def test_bitmidi_download_redirects_cannot_escape_provider(monkeypatch, destination):
    calls = []
    def respond(request):
        calls.append(request)
        if request.url.path == '/api/midi/get':
            return httpx.Response(200, json={'result': {'result': RECORD}})
        return httpx.Response(302, headers={'location': destination})
    mock_http(monkeypatch, respond)
    with pytest.raises(HTTPException):
        asyncio.run(bitmidi.download_source('https://bitmidi.com/uploads/123.mid', 'Draft'))
    assert len(calls) == 2


def test_bitmidi_mismatched_source_page_rejected_before_download(monkeypatch):
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={'result': {'result': RECORD}})
    mock_http(monkeypatch, respond)
    with pytest.raises(HTTPException) as error:
        asyncio.run(bitmidi.download_source('https://bitmidi.com/uploads/123.mid', 'Draft', 'https://bitmidi.com/different-midi-mid'))
    assert error.value.status_code == 422
    assert len(calls) == 1


def test_archive_download_cannot_redirect_into_bitmidi(monkeypatch):
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(302, headers={'location': 'https://bitmidi.com/uploads/123.mid'})
    mock_http(monkeypatch, respond)
    with pytest.raises(HTTPException) as error:
        asyncio.run(score_sources.download_source('https://archive.org/download/id/file.mid', 'Draft'))
    assert error.value.status_code == 400
    assert len(calls) == 1


def test_bitmidi_download_bounds_and_magic(monkeypatch):
    def respond(request):
        if request.url.path == '/api/midi/get':
            return httpx.Response(200, json={'result': {'result': RECORD}})
        return httpx.Response(200, headers={'content-length': str(bitmidi.MAX_MIDI_BYTES + 1)}, content=b'MThd')
    mock_http(monkeypatch, respond)
    with pytest.raises(HTTPException) as error:
        asyncio.run(bitmidi.download_source('https://bitmidi.com/uploads/123.mid', 'Draft'))
    assert error.value.status_code == 413


def test_official_underscore_slug_and_long_title_are_usable():
    # README's documented source slug preserves underscores.
    item = bitmidi.record({**RECORD, 'slug': 'passenger-let_her_go-mid', 'url': '/passenger-let_her_go-mid', 'name': 'A' * 255})
    assert item['page_url'] == 'https://bitmidi.com/passenger-let_her_go-mid'
    assert len(item['title']) == 200
    assert len(item['original_title']) == 255


def test_bitmidi_non_midi_document_rejected(monkeypatch):
    def respond(request):
        if request.url.path == '/api/midi/get':
            return httpx.Response(200, json={'result': {'result': RECORD}})
        return httpx.Response(200, content=b'<html>verification</html>')
    mock_http(monkeypatch, respond)
    with pytest.raises(HTTPException) as error:
        asyncio.run(bitmidi.download_source('https://bitmidi.com/uploads/123.mid', 'Draft'))
    assert error.value.status_code == 422


def test_bitmidi_invalid_api_shape_reports_unavailable(monkeypatch):
    mock_http(monkeypatch, lambda request: httpx.Response(200, json={'result': []}))
    with pytest.raises(HTTPException) as error:
        asyncio.run(bitmidi.search('test'))
    assert error.value.status_code == 502


def test_generation_downloads_once_then_uses_private_cache(client, monkeypatch):
    from backend import midi_cache
    downloads = []
    def respond(request):
        if request.url.path == '/api/midi/get':
            return httpx.Response(200, json={'result': {'result': RECORD}})
        downloads.append(request.url.path)
        return httpx.Response(200, content=fixture_midi())
    mock_http(monkeypatch, respond)
    body = {'midi_url': 'https://bitmidi.com/uploads/123.mid', 'source_page_url': 'https://bitmidi.com/moonlight-sonata-mid', 'title': 'Draft', 'rights_confirmed': True}
    first = client.post('/api/scores/from-source', json=body)
    second = client.post('/api/scores/from-source', json=body)
    assert first.status_code == second.status_code == 200
    assert first.json()['source']['cached'] is False
    assert second.json()['source']['cached'] is True
    assert len(downloads) == 1
    root = service.STATE_DIR / 'midi-cache'
    path = midi_cache.cache_path(root, body['midi_url'])
    assert path.stat().st_mode & 0o777 == 0o600
    assert root.stat().st_mode & 0o777 == 0o700
    bad = client.post('/api/scores/from-source', json={**body, 'source_page_url': 'https://bitmidi.com/wrong-mid'})
    assert bad.status_code == 422
    assert len(downloads) == 1
    path.write_bytes(b'corrupt cache')
    assert client.post('/api/scores/from-source', json=body).status_code == 200
    assert len(downloads) == 2
    import os, time
    expired = time.time() - midi_cache.TTL_SECONDS - 1
    os.utime(path, (expired, expired))
    assert client.post('/api/scores/from-source', json=body).status_code == 200
    assert len(downloads) == 3


def test_cache_limit_corruption_symlinks_and_unwritable_fallback(tmp_path, monkeypatch):
    from backend import midi_cache
    root = tmp_path / 'cache'
    monkeypatch.setattr(midi_cache, 'MAX_ENTRIES', 2)
    for number in range(4):
        midi_cache.write(root, str(number), fixture_midi())
    assert len(list(root.glob('*.cached'))) == 2
    assert midi_cache.read(root, '3') == fixture_midi()
    path = midi_cache.cache_path(root, '3')
    original = path.read_bytes()
    path.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
    assert midi_cache.read(root, '3') is None
    outside = tmp_path / 'outside'
    outside.mkdir()
    alias = tmp_path / 'alias'
    alias.symlink_to(outside, target_is_directory=True)
    midi_cache.write(alias, 'url', fixture_midi())
    assert list(outside.iterdir()) == []
    assert midi_cache.read(alias, 'url') is None
    blocked = tmp_path / 'file-not-directory'
    blocked.write_text('original')
    midi_cache.write(blocked, 'url', fixture_midi())
    assert blocked.read_text() == 'original'
    assert midi_cache.read(blocked, 'url') is None
