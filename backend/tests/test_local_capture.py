import time

import httpx
import pytest
from fastapi.testclient import TestClient

from backend import app as service
from backend import icloud_storage as cloud


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(service, 'STATE_DIR', tmp_path / 'state')
    return TestClient(service.app)


@pytest.fixture
def local_mac(client, tmp_path, monkeypatch):
    root = tmp_path / 'iCloud'
    root.mkdir()
    monkeypatch.setattr(service, 'ICLOUD_ROOT', root)
    monkeypatch.setattr(cloud.platform, 'system', lambda: 'Darwin')
    assert client.put('/api/settings', json={'storage_provider': 'icloud'}).status_code == 200
    return client, root


def test_local_upload_rights_and_pdf_type_required(client):
    assert client.post('/api/upload-local?title=Song', content=b'%PDF-1.7', headers={'Content-Type': 'application/pdf'}).status_code == 400
    assert client.post('/api/upload-local?title=Song&rights_confirmed=true', content=b'%PDF-1.7', headers={'Content-Type': 'text/plain'}).status_code == 415
    assert client.post('/api/upload-local?title=%20&rights_confirmed=true', content=b'%PDF-1.7', headers={'Content-Type': 'application/pdf'}).status_code == 400


def test_local_upload_icloud_saved_real_contents_and_no_remote_fetch(local_mac, monkeypatch):
    client, root = local_mac
    async def forbidden(url):
        raise AssertionError('Local PDF must never be fetched remotely')
    monkeypatch.setattr(service, 'fetch_pdf', forbidden)
    content = b'%PDF-1.7\nuser-purchased-or-authorized-score'
    response = client.post('/api/upload-local', params={'title': 'Local.pdf', 'rights_confirmed': True}, content=content, headers={'Content-Type': 'application/pdf'})
    assert response.status_code == 200
    assert (root / 'Piano Uploader' / 'Local.pdf').read_bytes() == content
    assert response.json()['storage_provider'] == 'icloud'
    assert 'not verified' in response.json()['message']


def test_local_upload_rejects_non_pdf_before_save(local_mac):
    client, root = local_mac
    response = client.post('/api/upload-local?title=Fake&rights_confirmed=true', content=b'<html>not pdf</html>', headers={'Content-Type': 'application/pdf'})
    assert response.status_code == 422
    assert not (root / 'Piano Uploader').exists()


def test_local_upload_size_limit_content_length_and_stream(local_mac, monkeypatch):
    client, root = local_mac
    monkeypatch.setattr(service, 'MAX_PDF_BYTES', 16)
    response = client.post('/api/upload-local?title=Large&rights_confirmed=true', content=b'%PDF-1.7' + b'x' * 20, headers={'Content-Type': 'application/pdf'})
    assert response.status_code == 413
    streamed = client.post('/api/upload-local?title=Large&rights_confirmed=true', content=iter([b'%PDF-1.7', b'x' * 20]), headers={'Content-Type': 'application/pdf'})
    assert streamed.status_code == 413
    assert not (root / 'Piano Uploader').exists()


def test_local_upload_google_multipart_and_destination(client, monkeypatch):
    service.put_value('settings', {'folder_id': 'destination'})
    service.put_value('google_token', {'access_token': 'test-token', 'expires_at': time.time() + 3600})
    original = httpx.AsyncClient
    calls = []
    def respond(request):
        calls.append(request)
        assert request.url.host == 'www.googleapis.com'
        assert request.headers['authorization'] == 'Bearer test-token'
        assert b'"parents": ["destination"]' in request.content
        assert b'"name": "User Score.pdf"' in request.content
        assert b'%PDF-1.7\nlocal-body' in request.content
        return httpx.Response(200, json={'id': 'saved', 'name': 'User Score.pdf'})
    monkeypatch.setattr(service.httpx, 'AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs))
    response = client.post('/api/upload-local', params={'title': 'User Score', 'rights_confirmed': True}, content=b'%PDF-1.7\nlocal-body', headers={'Content-Type': 'application/pdf'})
    assert response.status_code == 200
    assert response.json()['storage_provider'] == 'google'
    assert len(calls) == 1


def test_browser_capture_canonicalizes_and_deduplicates_score_links(client):
    payload = {'provider': 'musescore', 'page_url': 'https://musescore.com/sheetmusic?text=Moonlight', 'results': [
        {'title': '  Moonlight   Sonata ', 'page_url': 'https://www.musescore.com/user/123/scores/456?tracking=one#preview'},
        {'title': 'Duplicate', 'page_url': 'https://musescore.com/user/123/scores/456'},
    ]}
    response = client.post('/api/search/capture', json=payload)
    assert response.status_code == 200
    data = response.json()
    assert len(data['results']) == 1
    assert data['results'][0]['title'] == 'Moonlight Sonata'
    assert data['results'][0]['page_url'] == 'https://musescore.com/user/123/scores/456'
    assert data['results'][0]['pdf_url'] == ''
    assert 'not verified' in data['providers'][0]['message']


def test_musicnotes_browser_capture_returns_product_only(client):
    response = client.post('/api/search/capture', json={'provider': 'musicnotes', 'page_url': 'https://www.musicnotes.com/search/go?w=Prelude', 'results': [{'title': 'Prelude', 'page_url': 'https://musicnotes.com/sheetmusic/mtd.asp?ppn=MN12345&utm_source=test'}]})
    assert response.status_code == 200
    result = response.json()['results'][0]
    assert result['id'] == 'musicnotes:MN12345'
    assert result['page_url'] == 'https://www.musicnotes.com/sheetmusic/mtd.asp?ppn=MN12345'
    assert result['pdf_url'] == ''


@pytest.mark.parametrize('url', ['https://evil.example/user/1/scores/2', 'javascript:alert(1)', 'http://musescore.com/user/1/scores/2', 'https://musescore.com:8443/user/1/scores/2', 'https://musescore.com:bad/user/1/scores/2', 'https://user:secret@musescore.com/user/1/scores/2', 'https://musescore.com/sheetmusic?text=test'])
def test_capture_rejects_foreign_unsafe_and_search_links_as_results(client, url):
    response = client.post('/api/search/capture', json={'provider': 'musescore', 'page_url': 'https://musescore.com/sheetmusic?text=test', 'results': [{'title': 'Bad', 'page_url': url}]})
    assert response.status_code == 422


def test_capture_validates_source_search_page_provider_and_limit(client):
    payload = {'provider': 'musescore', 'page_url': 'https://musicnotes.com/search/go?w=test', 'results': []}
    assert client.post('/api/search/capture', json=payload).status_code == 422
    payload['page_url'] = 'https://musescore.com/user/1'
    assert client.post('/api/search/capture', json=payload).status_code == 422
    payload['page_url'] = 'https://musescore.com/sheetmusic?text=test'
    payload['results'] = [{'title': 'Song', 'page_url': 'https://musescore.com/user/1/scores/2'}] * 51
    assert client.post('/api/search/capture', json=payload).status_code == 422
    payload['results'] = []
    payload['provider'] = 'archive'
    assert client.post('/api/search/capture', json=payload).status_code == 422
