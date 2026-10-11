"""Public source search adapters; paid and restricted scores remain source links."""
import asyncio
import re
from html.parser import HTMLParser
from urllib.parse import quote, urlencode, urljoin, urlparse

import httpx
from fastapi import HTTPException

PROVIDERS = {
    'archive': 'Internet Archive',
    'imslp': 'IMSLP',
    'musicnotes': 'Musicnotes',
    'musescore': 'MuseScore',
}


def search_url(provider, query):
    if provider == 'archive':
        return 'https://archive.org/search?' + urlencode({'query': query + ' sheet music'})
    if provider == 'imslp':
        return 'https://imslp.org/index.php?' + urlencode({'title': 'Special:Search', 'search': query})
    if provider == 'musicnotes':
        return 'https://www.musicnotes.com/search/go?' + urlencode({'w': query})
    return 'https://musescore.com/sheetmusic?' + urlencode({'text': query})


def result(provider, identifier, title, page_url, creator='See source'):
    return {'id': provider + ':' + str(identifier), 'title': title, 'creator': creator,
            'source': PROVIDERS[provider], 'page_url': page_url, 'pdf_url': '',
            'thumbnail_url': '', 'license': 'Check licensing and access requirements on the source'}


class ProductLinks(HTMLParser):
    """Read public product links only; never decode embedded download payloads."""
    def __init__(self, provider):
        super().__init__(convert_charrefs=True)
        self.provider = provider
        self.active = None
        self.records = []
        self.seen = set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'a':
            self.active = None
            href = attrs.get('href', '')
            base = 'https://www.musicnotes.com' if self.provider == 'musicnotes' else 'https://musescore.com'
            url = urljoin(base, href)
            parsed = urlparse(url)
            hosts = {'www.musicnotes.com', 'musicnotes.com'} if self.provider == 'musicnotes' else {'musescore.com', 'www.musescore.com'}
            try:
                valid_port = parsed.port in (None, 443)
            except ValueError:
                valid_port = False
            if parsed.scheme != 'https' or parsed.hostname not in hosts or parsed.username or parsed.password or not valid_port:
                return
            if self.provider == 'musicnotes':
                match = re.search(r'(?:[?&]ppn=)([a-zA-Z0-9]+)', parsed.query and '?' + parsed.query, re.I)
                if parsed.path.lower() != '/sheetmusic/mtd.asp' or not match:
                    return
                identifier = match.group(1)
            else:
                match = re.fullmatch(r'/user/([0-9]+)/scores/([0-9]+)/?', parsed.path)
                if not match:
                    return
                identifier = match.group(1) + ':' + match.group(2)
            self.active = {'id': identifier, 'url': url, 'parts': [], 'label': attrs.get('title') or attrs.get('aria-label') or ''}
        elif tag == 'img' and self.active and attrs.get('alt'):
            self.active['parts'].append(attrs['alt'])

    def handle_data(self, data):
        if self.active:
            self.active['parts'].append(data)

    def handle_endtag(self, tag):
        if tag == 'a' and self.active:
            title = self.active['label'] or ' '.join(self.active['parts'])
            title = ' '.join(title.split()).strip()
            if title and title.lower() not in {'view', 'details', 'download', 'view score', 'sheet music'} and self.active['id'] not in self.seen:
                self.seen.add(self.active['id'])
                self.records.append(result(self.provider, self.active['id'], title[:250], self.active['url']))
            self.active = None


async def public_get(client, url, provider, params=None):
    allowed = {'imslp': {'imslp.org', 'www.imslp.org'}, 'musicnotes': {'musicnotes.com', 'www.musicnotes.com'}, 'musescore': {'musescore.com', 'www.musescore.com'}}[provider]
    limit = 5 * 1024 * 1024
    for _ in range(4):
        try:
            parsed = urlparse(url)
            valid = parsed.scheme == 'https' and parsed.hostname in allowed and parsed.port in (None, 443) and not parsed.username and not parsed.password
        except ValueError:
            valid = False
        if not valid:
            raise ValueError('Source redirected outside its public website.')
        async with client.stream('GET', url, params=params) as response:
            params = None
            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get('location')
                if not location:
                    raise ValueError('Source redirect missing destination.')
                url = urljoin(str(response.url), location)
                continue
            response.raise_for_status()
            if 'friendlytest' in urlparse(str(response.url)).path.lower():
                raise ValueError('Source requires a browser verification challenge.')
            length = response.headers.get('content-length', '')
            if length.isdigit() and int(length) > limit:
                raise ValueError('Source response too large.')
            chunks = []
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > limit:
                    raise ValueError('Source response too large.')
                chunks.append(chunk)
            headers = dict(response.headers)
            headers.pop('content-encoding', None)
            headers['content-length'] = str(size)
            return httpx.Response(response.status_code, content=b''.join(chunks), headers=headers, request=response.request)
    raise ValueError('Too many source redirects.')


async def source_search(provider, query):
    async with httpx.AsyncClient(timeout=12, follow_redirects=False, headers={'User-Agent': 'PianoUploader/0.1 (public sheet music discovery)'}) as client:
        if provider == 'imslp':
            response = await public_get(client, 'https://imslp.org/api.php', provider, {'action': 'query', 'list': 'search', 'srsearch': query, 'srnamespace': 0, 'srlimit': 12, 'format': 'json'})
            data = response.json()
            query_data = data.get('query') if isinstance(data, dict) else None
            if not isinstance(data, dict) or 'error' in data or not isinstance(query_data, dict) or not isinstance(query_data.get('search'), list):
                raise ValueError('The public IMSLP search API is unavailable.')
            records = []
            for item in query_data['search'][:12]:
                if not isinstance(item, dict):
                    continue
                title = item.get('title', '')
                if not isinstance(title, str) or not title:
                    continue
                composer = re.search(r'\(([^()]*)\)$', title)
                records.append(result(provider, item.get('pageid') or title, title, 'https://imslp.org/wiki/' + quote(title.replace(' ', '_'), safe=''), composer.group(1) if composer else 'See source'))
            return records
        response = await public_get(client, search_url(provider, query), provider)
        if 'text/html' not in response.headers.get('content-type', '').lower():
            raise ValueError('The source did not return a public search page.')
        title = re.search(r'<title[^>]*>(.*?)</title>', response.text, re.I | re.S)
        if title and any(marker in title.group(1).lower() for marker in ['just a moment', 'access denied', 'security check', 'attention required']):
            raise ValueError('Source requires browser verification.')
        parser = ProductLinks(provider)
        parser.feed(response.text)
        return parser.records[:12]


async def search_all(query, sources, archive_search):
    chosen = list(PROVIDERS) if sources is None else list(dict.fromkeys(part.strip() for part in sources.split(',') if part.strip()))
    if not chosen or any(provider not in PROVIDERS for provider in chosen):
        raise HTTPException(400, 'Choose sources from archive, imslp, musicnotes, musescore.')
    async def run(provider):
        metadata = {'id': provider, 'name': PROVIDERS[provider], 'search_url': search_url(provider, query), 'result_count': 0}
        try:
            if provider == 'archive':
                data = await asyncio.wait_for(archive_search(query), timeout=18)
                records = data['results']
                if data.get('error'):
                    metadata['message'] = data['error']
            else:
                records = await asyncio.wait_for(source_search(provider, query), timeout=15)
            metadata['result_count'] = len(records)
            metadata['status'] = 'ok' if records or provider in {'archive', 'imslp'} else 'external'
            if metadata['status'] == 'external':
                metadata['message'] = 'No public product records were available. Continue searching on the source website; access or purchase may be required.'
            return records, metadata
        except (httpx.HTTPError, ValueError, KeyError, HTTPException, asyncio.TimeoutError):
            metadata.update(status='unavailable', message='Public search could not be reached or read. Open the source website to search; account or purchase requirements may apply.')
            return [], metadata
    resolved = await asyncio.gather(*(run(provider) for provider in chosen))
    results = [record for records, _ in resolved for record in records]
    metadata = [meta for _, meta in resolved]
    failed = [meta['name'] for meta in metadata if meta['status'] == 'unavailable']
    return {'results': results, 'providers': metadata, **({'error': 'Public search unavailable for: ' + ', '.join(failed) + '. Use the source links below.'} if failed else {})}
