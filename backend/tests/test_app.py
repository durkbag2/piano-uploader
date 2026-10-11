import asyncio
import time

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend import app as service


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(service, 'STATE_DIR', tmp_path / 'state')
    monkeypatch.setattr(service, 'CLIENT_ID', 'test-client')
    monkeypatch.setattr(service, 'CLIENT_SECRET', 'test-secret')


@pytest.fixture
def client():
    # Background polling is exercised separately, preventing races in API tests.
    return TestClient(service.app)


def test_settings_validate_and_persist(client):
    assert client.put('/api/settings', json={'folder_id': 'abc', 'poll_interval_seconds': 1}).status_code == 422
    assert client.put('/api/settings', json={'folder_id': "x' or true", 'poll_interval_seconds': 60}).status_code == 422
    payload = {'folder_id': 'my_folder-12', 'poll_interval_seconds': 30, 'polling_enabled': True}
    assert client.put('/api/settings', json=payload).json() == payload
    assert client.get('/api/settings').json() == payload


def test_queue_deduplicates_and_keeps_opened_status(client):
    song = {'id': 'drive1', 'title': 'Prelude.pdf', 'web_view_link': 'https://drive.google.com/file/d/drive1/view'}
    assert service.enqueue([song]) == 1
    assert service.enqueue([song]) == 0
    assert client.post('/api/queue/drive1/opened').json()['status'] == 'opened'
    assert service.enqueue([song]) == 0
    assert client.get('/api/queue').json()['items'][0]['status'] == 'opened'
    assert client.post('/api/queue/missing/opened').status_code == 404


def test_oauth_state_rejects_mismatch_expiration_and_replay(client):
    assert client.get('/api/auth/start').status_code == 200
    state = service.get_value('oauth_state')['value']
    assert client.get('/api/auth/callback', params={'state': 'wrong', 'code': 'x'}).status_code == 400
    assert client.get('/api/auth/callback', params={'state': state, 'error': 'access_denied'}, follow_redirects=False).status_code == 307
    assert client.get('/api/auth/callback', params={'state': state, 'error': 'access_denied'}).status_code == 400
    service.put_value('oauth_state', {'value': 'expired', 'expires_at': time.time() - 1})
    assert client.get('/api/auth/callback', params={'state': 'expired', 'code': 'x'}).status_code == 400


@pytest.mark.parametrize('url', [
    'http://archive.org/download/foo.pdf', 'https://archive.org.evil.example/a.pdf',
    'https://127.0.0.1/a.pdf', 'https://archive.org:8443/a.pdf',
    'https://user:password@archive.org/a.pdf', 'file:///etc/passwd',
])
def test_pdf_ssrf_url_rejection(url):
    with pytest.raises(HTTPException) as error:
        service.validate_pdf_url(url)
    assert error.value.status_code == 400


def mock_http(monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr(service.httpx, 'AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))


def test_pdf_validates_redirects_without_fetching_private_destination(monkeypatch):
    calls = []
    def respond(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={'location': 'https://127.0.0.1/private'})
    mock_http(monkeypatch, respond)
    with pytest.raises(HTTPException) as error:
        asyncio.run(service.fetch_pdf('https://archive.org/download/a.pdf'))
    assert error.value.status_code == 400
    assert len(calls) == 1


def test_pdf_rejects_html_and_limits_size(monkeypatch):
    mock_http(monkeypatch, lambda request: httpx.Response(200, content=b'<html>blocked</html>'))
    with pytest.raises(HTTPException) as error:
        asyncio.run(service.fetch_pdf('https://archive.org/a.pdf'))
    assert error.value.status_code == 422


def test_pdf_enforces_size_before_reading(monkeypatch):
    mock_http(monkeypatch, lambda request: httpx.Response(200, headers={'content-length': str(service.MAX_PDF_BYTES + 1)}, content=b'%PDF-1.7'))
    with pytest.raises(HTTPException) as error:
        asyncio.run(service.fetch_pdf('https://archive.org/a.pdf'))
    assert error.value.status_code == 413


def test_pdf_accepts_valid_document(monkeypatch):
    content = b'%PDF-1.7\nexample'
    mock_http(monkeypatch, lambda request: httpx.Response(200, content=content))
    assert asyncio.run(service.fetch_pdf('https://archive.org/a.pdf')) == content


def test_poll_adds_drive_files_once(monkeypatch):
    async def files():
        return [{'id': 'a', 'title': 'Nocturne.pdf', 'web_view_link': 'https://drive.google.com/file/d/a/view'}]
    monkeypatch.setattr(service, 'drive_files', files)
    assert asyncio.run(service.poll())['added'] == 1
    assert asyncio.run(service.poll())['added'] == 0
    assert service.get_value('last_poll_at')


def test_disconnected_library_and_unconfirmed_upload_fail(client):
    assert client.get('/api/library').status_code == 400
    assert client.post('/api/upload', json={'title': 'song', 'pdf_url': 'https://archive.org/song.pdf', 'rights_confirmed': False}).status_code == 400


def test_foreign_origin_and_host_rejected(client):
    assert client.post('/api/auth/disconnect', headers={'Origin': 'https://evil.example'}).status_code == 403
    assert client.get('/api/status', headers={'Host': 'evil.example'}).status_code == 403
    assert client.get('/api/status').json()['yamaha_auto_import_supported'] is False


def test_oauth_token_exchange_never_exposes_secrets(monkeypatch, client):
    def respond(request):
        assert request.url.host == 'oauth2.googleapis.com'
        return httpx.Response(200, json={'access_token': 'access-value', 'refresh_token': 'refresh-value', 'expires_in': 3600})
    mock_http(monkeypatch, respond)
    client.get('/api/auth/start')
    state = service.get_value('oauth_state')['value']
    response = client.get('/api/auth/callback', params={'state': state, 'code': 'code'}, follow_redirects=False)
    assert response.headers['location'] == 'http://localhost:5173/?auth=connected'
    status = client.get('/api/status')
    assert status.json()['google_connected'] is True
    assert 'access-value' not in status.text and 'refresh-value' not in status.text


def test_drive_pagination_preserves_authorization_boundary(monkeypatch):
    service.put_value('settings', {'folder_id': 'folder', 'poll_interval_seconds': 60, 'polling_enabled': False})
    service.put_value('google_token', {'access_token': 'test-access', 'expires_at': time.time() + 3600})
    calls = []
    def respond(request):
        assert request.url.host == 'www.googleapis.com'
        assert request.headers['authorization'] == 'Bearer test-access'
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(200, json={'nextPageToken': 'page2', 'files': [{'id': 'one', 'name': 'One.pdf'}]})
        assert request.url.params['pageToken'] == 'page2'
        return httpx.Response(200, json={'files': [{'id': 'two', 'name': 'Two.pdf'}]})
    mock_http(monkeypatch, respond)
    songs = asyncio.run(service.drive_files())
    assert [song['id'] for song in songs] == ['one', 'two']


def test_invalid_pdf_port_returns_validation_error():
    with pytest.raises(HTTPException) as error:
        service.validate_pdf_url('https://archive.org:bad/a.pdf')
    assert error.value.status_code == 400


def test_queue_isolates_folders_and_restores_handoffs(client):
    song = {'id': 'one', 'title': 'Song.pdf', 'web_view_link': 'https://drive.google.com/file/d/one/view'}
    service.put_value('settings', {'folder_id': 'folderA'})
    service.enqueue([song])
    first = service.queue_items()[0]
    assert client.post('/api/queue/' + first['id'] + '/opened').status_code == 200
    service.put_value('settings', {'folder_id': 'folderB'})
    assert service.queue_items() == []
    assert service.enqueue([song]) == 1
    assert service.queue_items()[0]['status'] == 'pending'
    service.put_value('settings', {'folder_id': 'folderA'})
    assert service.queue_items()[0]['status'] == 'opened'


def test_upload_posts_pdf_and_folder_metadata(monkeypatch, client):
    service.put_value('settings', {'folder_id': 'destination'})
    service.put_value('google_token', {'access_token': 'test-access', 'expires_at': time.time() + 3600})
    requests = []
    def respond(request):
        requests.append(request)
        if request.url.host == 'archive.org':
            assert 'authorization' not in request.headers
            return httpx.Response(200, content=b'%PDF-1.7\nsheet')
        assert request.url.host == 'www.googleapis.com'
        assert request.headers['authorization'] == 'Bearer test-access'
        assert request.headers['content-type'].startswith('multipart/related; boundary=')
        assert b'"parents": ["destination"]' in request.content
        assert b'%PDF-1.7\nsheet' in request.content
        return httpx.Response(200, json={'id': 'created', 'name': 'Prelude.pdf', 'webViewLink': 'https://drive.google.com/file/d/created/view'})
    mock_http(monkeypatch, respond)
    response = client.post('/api/upload', json={'title': 'Prelude', 'pdf_url': 'https://archive.org/sheet.pdf', 'rights_confirmed': True})
    assert response.status_code == 200
    assert response.json()['id'] == 'created'
    assert len(requests) == 2


def test_polling_honors_enabled_setting_and_interval(monkeypatch):
    service.put_value('settings', {'folder_id': 'folder', 'poll_interval_seconds': 30, 'polling_enabled': False})
    service.put_value('google_token', {'access_token': 'token'})
    calls = []
    async def files():
        calls.append('poll')
        return []
    monkeypatch.setattr(service, 'drive_files', files)
    asyncio.run(service.poll_tick())
    assert calls == []
    service.put_value('settings', {'folder_id': 'folder', 'poll_interval_seconds': 30, 'polling_enabled': True})
    asyncio.run(service.poll_tick())
    assert calls == ['poll']
    assert service.get_value('next_poll_epoch') > time.time() + 28
    asyncio.run(service.poll_tick())
    assert calls == ['poll']
    service.put_value('next_poll_epoch', time.time() - 1)
    asyncio.run(service.poll_tick())
    assert calls == ['poll', 'poll']


def test_thumbnail_proxies_jpeg_without_credentials(monkeypatch, client):
    jpeg = b'\xff\xd8\xff\xe0thumbnail'
    def respond(request):
        assert request.url.host == 'archive.org'
        assert 'authorization' not in request.headers
        return httpx.Response(200, content=jpeg, headers={'content-type': 'image/jpeg'})
    mock_http(monkeypatch, respond)
    response = client.get('/api/thumbnail', params={'url': 'https://archive.org/services/img/song'})
    assert response.status_code == 200
    assert response.content == jpeg
    assert response.headers['content-type'] == 'image/jpeg'
    assert response.headers['x-content-type-options'] == 'nosniff'


@pytest.mark.parametrize('content,mime', [(b'<html>error</html>', 'text/html'), (b'<svg/>', 'image/svg+xml'), (b'<html/>', 'image/jpeg')])
def test_thumbnail_rejects_html_svg_and_spoofed_content(monkeypatch, content, mime):
    mock_http(monkeypatch, lambda request: httpx.Response(200, content=content, headers={'content-type': mime}))
    with pytest.raises(HTTPException) as error:
        asyncio.run(service.fetch_thumbnail('https://archive.org/services/img/song'))
    assert error.value.status_code == 422


def test_thumbnail_rejects_foreign_redirect_before_fetch(monkeypatch):
    calls = []
    def respond(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={'location': 'https://evil.example/image.jpg'})
    mock_http(monkeypatch, respond)
    with pytest.raises(HTTPException) as error:
        asyncio.run(service.fetch_thumbnail('https://archive.org/services/img/song'))
    assert error.value.status_code == 400
    assert len(calls) == 1


def test_thumbnail_limits_size_and_initial_path(monkeypatch):
    mock_http(monkeypatch, lambda request: httpx.Response(200, content=b'\xff\xd8\xff', headers={'content-type': 'image/jpeg', 'content-length': str(2 * 1024 * 1024 + 1)}))
    with pytest.raises(HTTPException) as error:
        asyncio.run(service.fetch_thumbnail('https://archive.org/services/img/song'))
    assert error.value.status_code == 413
    with pytest.raises(HTTPException) as error:
        asyncio.run(service.fetch_thumbnail('https://archive.org/arbitrary/path'))
    assert error.value.status_code == 400
