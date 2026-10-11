import asyncio
import json
import sqlite3

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend import app as service
from backend import icloud_storage as cloud


@pytest.fixture
def mac(tmp_path, monkeypatch):
    root = tmp_path / 'iCloud'
    root.mkdir()
    monkeypatch.setattr(service, 'ICLOUD_ROOT', root)
    monkeypatch.setattr(service, 'STATE_DIR', tmp_path / 'state')
    monkeypatch.setattr(cloud.platform, 'system', lambda: 'Darwin')
    return root, TestClient(service.app)


def select(client, folder='Piano Uploader'):
    response = client.put('/api/settings', json={'storage_provider': 'icloud', 'icloud_folder': folder, 'folder_id': 'google-kept', 'polling_enabled': True, 'poll_interval_seconds': 30})
    assert response.status_code == 200, response.text
    return response.json()


def test_default_settings_and_legacy_google_queue_migration(mac):
    root, client = mac
    state = service.STATE_DIR
    state.mkdir()
    conn = sqlite3.connect(state / 'state.sqlite3')
    conn.execute('CREATE TABLE kv (key TEXT PRIMARY KEY,value TEXT NOT NULL)')
    conn.execute('INSERT INTO kv VALUES (?,?)', ('settings', json.dumps({'folder_id': 'old-folder'})))
    conn.execute('CREATE TABLE queue (id TEXT PRIMARY KEY,drive_id TEXT UNIQUE,title TEXT,status TEXT,web_view_link TEXT,created_at TEXT)')
    conn.execute('INSERT INTO queue VALUES (?,?,?,?,?,?)', ('old-id', 'old-id', 'Old.pdf', 'opened', 'https://drive.google.com/file/d/old-id/view', '2026-01-01'))
    conn.commit()
    conn.close()
    settings = client.get('/api/settings').json()
    assert settings['storage_provider'] == 'google'
    assert settings['icloud_folder'] == 'Piano Uploader'
    assert client.get('/api/queue').json()['items'][0]['status'] == 'opened'
    assert client.get('/api/queue').json()['items'][0]['storage_provider'] == 'google'
    select(client)
    assert client.get('/api/queue').json()['items'] == []
    assert client.get('/api/settings').json()['folder_id'] == 'google-kept'


@pytest.mark.parametrize('folder', ['/outside', '../outside', 'good/../outside', 'good//bad', './inside', 'good\\bad', 'bad\x00name', 'bad\nname', 'bad:name'])
def test_unsafe_icloud_folders_rejected(mac, folder):
    root, client = mac
    response = client.put('/api/settings', json={'storage_provider': 'icloud', 'icloud_folder': folder})
    assert response.status_code == 422
    assert client.get('/api/settings').json()['storage_provider'] == 'google'


def test_icloud_folder_symlink_escape_rejected(mac, tmp_path):
    root, client = mac
    outside = tmp_path / 'outside'
    outside.mkdir()
    (root / 'alias').symlink_to(outside, target_is_directory=True)
    assert client.put('/api/settings', json={'storage_provider': 'icloud', 'icloud_folder': 'alias/scores'}).status_code == 422
    assert not (outside / 'scores').exists()


def test_missing_icloud_root_and_non_mac_do_not_claim_success(mac, monkeypatch):
    root, client = mac
    monkeypatch.setattr(cloud.platform, 'system', lambda: 'Linux')
    assert client.get('/api/status').json()['icloud_available'] is False
    assert client.put('/api/settings', json={'storage_provider': 'icloud'}).status_code == 422
    monkeypatch.setattr(cloud.platform, 'system', lambda: 'Darwin')
    root.rmdir()
    assert client.get('/api/status').json()['icloud_available'] is False
    assert client.put('/api/settings', json={'storage_provider': 'icloud'}).status_code == 422


def test_write_access_required_for_availability(mac, monkeypatch):
    root, client = mac
    monkeypatch.setattr(cloud.os, 'access', lambda *args: False)
    assert client.get('/api/status').json()['icloud_available'] is False


def test_upload_sanitizes_filename_collision_and_preserves_pdf(mac, monkeypatch):
    root, client = mac
    select(client, 'Music/Scores')
    content = b'%PDF-1.7\ncomplete-pdf'
    async def fetch(url):
        return content
    monkeypatch.setattr(service, 'fetch_pdf', fetch)
    body = {'pdf_url': 'https://archive.org/score.pdf', 'title': '../Moonlight:/\x01.pdf', 'rights_confirmed': True}
    first = client.post('/api/upload', json=body)
    second = client.post('/api/upload', json=body)
    assert first.status_code == second.status_code == 200
    a, b = first.json(), second.json()
    assert a['title'] != b['title']
    folder = root / 'Music' / 'Scores'
    assert (folder / a['title']).read_bytes() == content
    assert (folder / b['title']).read_bytes() == content
    assert '/' not in a['title'] and '\x01' not in a['title']
    assert a['storage_provider'] == 'icloud'
    assert 'not verified' in a['message']
    listing = client.get('/api/library').json()
    assert len(listing['songs']) == 2
    assert all(song['storage_provider'] == 'icloud' for song in listing['songs'])
    assert client.get(a['web_view_link']).content == content
    assert client.get('/api/icloud/files/not-an-id').status_code == 404
    assert not list(folder.glob('.piano-*'))


def test_listing_excludes_placeholders_invalid_files_and_symlinks(mac, tmp_path):
    root, client = mac
    select(client)
    folder = root / 'Piano Uploader'
    folder.mkdir()
    (folder / 'actual.pdf').write_bytes(b'%PDF-1.7\nreal')
    (folder / '.cloud.pdf.icloud').write_bytes(b'placeholder')
    (folder / 'fake.pdf').write_bytes(b'<html>not a pdf</html>')
    outside = tmp_path / 'outside.pdf'
    outside.write_bytes(b'%PDF-1.7\nprivate')
    (folder / 'link.pdf').symlink_to(outside)
    listing = client.get('/api/library').json()
    assert [song['title'] for song in listing['songs']] == ['actual.pdf']
    assert listing['placeholders'] == 1
    assert 'Download Now' in listing['message']
    assert client.get('/api/icloud/files/' + cloud.file_id('link.pdf')).status_code == 404


def test_icloud_poll_works_without_google_and_namespace_preserves_google(mac):
    root, client = mac
    service.put_value('settings', {'folder_id': 'google-kept'})
    service.enqueue([{'id': 'drive-one', 'title': 'Google.pdf', 'web_view_link': 'https://drive.google.com/file/d/drive-one/view'}])
    select(client)
    folder = root / 'Piano Uploader'
    folder.mkdir()
    (folder / 'Local.pdf').write_bytes(b'%PDF-1.7\nlocal')
    assert service.get_value('google_token') is None
    asyncio.run(service.poll_tick())
    items = client.get('/api/queue').json()['items']
    assert len(items) == 1
    assert items[0]['storage_provider'] == 'icloud'
    assert items[0]['folder_hint'] == 'Piano Uploader'
    assert client.post('/api/import-now').json()['added'] == 0
    assert client.post('/api/queue/' + items[0]['id'] + '/opened').status_code == 200
    assert client.post('/api/import-now').json()['items'][0]['status'] == 'opened'
    client.put('/api/settings', json={'storage_provider': 'google', 'folder_id': 'google-kept'})
    google = client.get('/api/queue').json()['items']
    assert len(google) == 1 and google[0]['id'] == 'google-kept:drive-one'
    assert client.get(items[0]['web_view_link']).status_code == 404


def test_missing_root_upload_fails_before_fetch(mac, monkeypatch):
    root, client = mac
    select(client)
    root.rmdir()
    async def forbidden(url):
        raise AssertionError('No download before validating local destination')
    monkeypatch.setattr(service, 'fetch_pdf', forbidden)
    response = client.post('/api/upload', json={'pdf_url': 'https://archive.org/score.pdf', 'title': 'Song', 'rights_confirmed': True})
    assert response.status_code == 422


def test_nested_icloud_folder_queue_opened_route(mac):
    root, client = mac
    select(client, 'Music/Scores')
    folder = root / 'Music' / 'Scores'
    folder.mkdir(parents=True)
    (folder / 'Song.pdf').write_bytes(b'%PDF-1.7\nlocal')
    item = client.post('/api/import-now').json()['items'][0]
    from urllib.parse import quote
    response = client.post('/api/queue/' + quote(item['id'], safe='') + '/opened')
    assert response.status_code == 200
    assert response.json()['status'] == 'opened'


def test_local_listing_reads_only_header(mac, monkeypatch):
    root, client = mac
    select(client)
    folder = root / 'Piano Uploader'
    folder.mkdir()
    (folder / 'Song.pdf').write_bytes(b'%PDF-1.7\n' + b'x' * 1024)
    original = cloud.read_pdf
    calls = []
    def inspected(path, maximum, header_only=False):
        calls.append(header_only)
        return original(path, maximum, header_only=header_only)
    monkeypatch.setattr(cloud, 'read_pdf', inspected)
    assert len(client.get('/api/library').json()['songs']) == 1
    assert calls == [True]


def test_atomic_publication_failure_cleans_reservation_without_overwriting(mac, monkeypatch):
    root, client = mac
    select(client)
    folder = root / 'Piano Uploader'
    folder.mkdir()
    (folder / 'Song.pdf').write_bytes(b'%PDF-1.7\nexisting')
    def fail(*args):
        raise OSError('filesystem rename failed')
    monkeypatch.setattr(cloud.os, 'replace', fail)
    with pytest.raises(HTTPException) as error:
        cloud.save_pdf(root, 'Piano Uploader', 'Song', b'%PDF-1.7\nnew', service.MAX_PDF_BYTES)
    assert error.value.status_code == 422
    assert (folder / 'Song.pdf').read_bytes() == b'%PDF-1.7\nexisting'
    assert not (folder / 'Song (2).pdf').exists()
    assert not list(folder.glob('.piano-*'))


def test_dataless_mac_pdf_is_reported_as_placeholder_without_open(mac, monkeypatch):
    from pathlib import Path
    from types import SimpleNamespace
    root, client = mac
    select(client)
    folder = root / 'Piano Uploader'
    folder.mkdir()
    cloud_only = folder / 'Cloud only.pdf'
    cloud_only.write_bytes(b'%PDF-1.7\nnot downloaded')
    original = Path.lstat
    def dataless(path, *args, **kwargs):
        info = original(path, *args, **kwargs)
        if path == cloud_only:
            return SimpleNamespace(st_mode=info.st_mode, st_flags=0x40000000)
        return info
    monkeypatch.setattr(Path, 'lstat', dataless)
    with pytest.raises(HTTPException) as error:
        cloud.read_pdf(cloud_only, service.MAX_PDF_BYTES)
    assert error.value.status_code == 409
    listing = client.get('/api/library').json()
    assert listing['songs'] == []
    assert listing['placeholders'] == 1
