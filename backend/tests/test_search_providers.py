"""Representative parser fixtures, not claims of live provider compatibility."""
import asyncio
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi import HTTPException

from backend import search_providers as providers


def test_provider_query_encoding():
    query = 'Moonlight & C# + Sonata?'
    for provider, key in [('imslp', 'search'), ('musicnotes', 'w'), ('musescore', 'text')]:
        assert parse_qs(urlparse(providers.search_url(provider, query)).query)[key] == [query]


def test_musicnotes_parser_reads_product_links_only():
    parser = providers.ProductLinks('musicnotes')
    parser.feed('''<a href="/sheetmusic/mtd.asp?ppn=MN0123456" title="Moonlight Sonata">Buy</a>
    <a href="https://evil.example/sheetmusic/mtd.asp?ppn=MN0001">Wrong host</a>
    <a href="javascript:alert(1)">Fake score</a><a href="/search/go?w=sonata">Navigation</a>
    <a href="/sheetmusic/mtd.asp?ppn=MN0123456">Duplicate</a>''')
    assert len(parser.records) == 1
    assert parser.records[0]['title'] == 'Moonlight Sonata'
    assert parser.records[0]['pdf_url'] == ''
    assert parser.records[0]['page_url'].startswith('https://www.musicnotes.com/')


def test_musescore_parser_reads_score_links_and_nested_labels():
    parser = providers.ProductLinks('musescore')
    parser.feed('''<a href="/user/123/scores/456"><span>Moonlight &amp; Prelude</span></a>
    <a href="https://evil.example/user/123/scores/999">Foreign score</a>
    <a href="/user/123">Profile</a><a href="/user/123/scores/456">Duplicate</a>''')
    assert len(parser.records) == 1
    assert parser.records[0]['title'] == 'Moonlight & Prelude'
    assert parser.records[0]['page_url'] == 'https://musescore.com/user/123/scores/456'


def test_sources_selection_isolates_failures(monkeypatch):
    async def archive(query):
        raise HTTPException(502, 'Archive unavailable')
    called = []
    async def source(provider, query):
        called.append(provider)
        return [providers.result(provider, 'real', 'A discovered record', 'https://imslp.org/wiki/Actual')]
    monkeypatch.setattr(providers, 'source_search', source)
    output = asyncio.run(providers.search_all('sonata', 'archive,imslp', archive))
    assert called == ['imslp']
    assert len(output['results']) == 1
    assert [entry['status'] for entry in output['providers']] == ['unavailable', 'ok']
    assert output['providers'][0]['result_count'] == 0


@pytest.mark.parametrize('sources', ['unknown', '', 'archive,wrong'])
def test_invalid_sources_rejected(sources):
    with pytest.raises(HTTPException) as error:
        asyncio.run(providers.search_all('test', sources, None))
    assert error.value.status_code == 400


def test_empty_public_product_page_returns_external_without_fake_cards(monkeypatch):
    async def source(provider, query):
        return []
    monkeypatch.setattr(providers, 'source_search', source)
    output = asyncio.run(providers.search_all('test', 'musicnotes,musescore', None))
    assert output['results'] == []
    assert all(entry['status'] == 'external' for entry in output['providers'])
    assert all(entry['search_url'].startswith('https://') for entry in output['providers'])


def mock_http(monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr(providers.httpx, 'AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))


def test_imslp_api_records_have_encoded_titles_and_no_downloads(monkeypatch):
    def respond(request):
        assert request.url.params['srsearch'] == 'Moonlight & sonata'
        assert 'authorization' not in request.headers
        return httpx.Response(200, json={'query': {'search': [{'pageid': 123, 'title': 'Sonata & Prelude (Beethoven, Ludwig van)'}]}})
    mock_http(monkeypatch, respond)
    records = asyncio.run(providers.source_search('imslp', 'Moonlight & sonata'))
    assert records[0]['creator'] == 'Beethoven, Ludwig van'
    assert '%26' in records[0]['page_url']
    assert records[0]['pdf_url'] == ''


def test_public_provider_redirect_cannot_fetch_foreign_destination(monkeypatch):
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(302, headers={'location': 'https://evil.example/steal'})
    mock_http(monkeypatch, respond)
    with pytest.raises((ValueError, httpx.HTTPError)):
        asyncio.run(providers.source_search('musicnotes', 'test'))
    assert len(calls) == 1


def test_imslp_browser_challenge_reported_unavailable(monkeypatch):
    def respond(request):
        if request.url.path == '/api.php':
            return httpx.Response(302, headers={'location': '/friendlytest.html'})
        return httpx.Response(200, headers={'content-type': 'text/html'}, text='<title>Verify browser</title>')
    mock_http(monkeypatch, respond)
    output = asyncio.run(providers.search_all('test', 'imslp', None))
    assert output['results'] == []
    assert output['providers'][0]['status'] == 'unavailable'


def test_imslp_actual_api_schema_without_pageid(monkeypatch):
    # Actual public API response shape observed during live verification.
    record = {'ns': 0, 'title': 'Moonlight, Op.54 No.3 (Palmgren, Selim)', 'snippet': '', 'size': 53, 'wordcount': 6, 'timestamp': '2007-08-17T02:06:24Z'}
    mock_http(monkeypatch, lambda request: httpx.Response(200, json={'query': {'search': [record]}}))
    results = asyncio.run(providers.source_search('imslp', 'moonlight'))
    assert len(results) == 1
    assert results[0]['title'] == record['title']
    assert results[0]['creator'] == 'Palmgren, Selim'
    assert results[0]['id'].startswith('imslp:Moonlight')


@pytest.mark.parametrize('destination', ['https://www.musicnotes.com:bad/search', 'https://www.musicnotes.com:8443/search', 'https://user:pass@www.musicnotes.com/search'])
def test_public_provider_rejects_unsafe_redirect_ports_credentials(monkeypatch, destination):
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(302, headers={'location': destination})
    mock_http(monkeypatch, respond)
    with pytest.raises((ValueError, httpx.HTTPError)):
        asyncio.run(providers.source_search('musicnotes', 'test'))
    assert len(calls) == 1


def test_public_source_stream_limit(monkeypatch):
    mock_http(monkeypatch, lambda request: httpx.Response(200, content=b'x' * (5 * 1024 * 1024 + 1), headers={'content-type': 'text/html'}))
    with pytest.raises(ValueError, match='too large'):
        asyncio.run(providers.source_search('musicnotes', 'test'))


@pytest.mark.parametrize('payload', [[], {'query': []}, {'query': {'search': {}}}])
def test_invalid_imslp_json_shapes_report_unavailable(monkeypatch, payload):
    mock_http(monkeypatch, lambda request: httpx.Response(200, json=payload))
    response = asyncio.run(providers.search_all('test', 'imslp', None))
    assert response['providers'][0]['status'] == 'unavailable'
    assert response['results'] == []


def test_imslp_ignores_malformed_records(monkeypatch):
    mock_http(monkeypatch, lambda request: httpx.Response(200, json={'query': {'search': [None, [], {'title': ['bad']}, {'title': 'Valid work'}]}}))
    records = asyncio.run(providers.source_search('imslp', 'test'))
    assert len(records) == 1
    assert records[0]['title'] == 'Valid work'


def test_product_parser_rejects_unsafe_ports_and_credentials():
    parser = providers.ProductLinks('musescore')
    parser.feed('''<a href="https://musescore.com:bad/user/1/scores/2">Invalid port</a>
    <a href="https://musescore.com:8443/user/1/scores/2">Alternate port</a>
    <a href="https://user:pass@musescore.com/user/1/scores/2">Credentials</a>''')
    assert parser.records == []
