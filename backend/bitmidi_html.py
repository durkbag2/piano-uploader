"""Read the site's ordinary search links and explicitly labeled MIDI downloads."""
import re
import unicodedata
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

from fastapi import HTTPException


def keywords(value):
    text = ''.join(c for c in unicodedata.normalize('NFKD', value.casefold()) if not unicodedata.combining(c))
    return set(re.findall(r'[^\W_]+', text))


def all_keywords(query, title):
    wanted = keywords(query)
    return bool(wanted) and wanted <= keywords(title)


def source_page(url):
    from .bitmidi import validate_url
    parsed = validate_url(url)
    if not re.fullmatch(r'/[a-z0-9][a-z0-9_-]*', parsed.path) or parsed.path in {'/search', '/about', '/random', '/privacy'} or parsed.query or parsed.fragment:
        raise ValueError('Not a BitMidi MIDI page.')
    return 'https://bitmidi.com' + parsed.path


class Links(HTMLParser):
    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base = base
        self.active = None
        self.links = []
        self.title_parts = []
        self.heading = False
        self.article_depth = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'article':
            self.article_depth += 1
        if tag == 'h1':
            self.heading = True
        if tag == 'a':
            self.active = {'url': urljoin(self.base, attrs.get('href', '')), 'label': attrs.get('title') or attrs.get('aria-label') or '', 'parts': [], 'download': 'download' in attrs, 'article': self.article_depth > 0}

    def handle_data(self, data):
        if self.active:
            self.active['parts'].append(data)
        if self.heading:
            self.title_parts.append(data)

    def handle_endtag(self, tag):
        if tag == 'a' and self.active:
            self.active['text'] = ' '.join(' '.join(self.active['parts']).split())
            self.links.append(self.active)
            self.active = None
        if tag == 'h1':
            self.heading = False
        if tag == 'article':
            self.article_depth = max(0, self.article_depth - 1)


def search_records(html, query):
    from .bitmidi import UNKNOWN_LICENSE
    parser = Links('https://bitmidi.com')
    parser.feed(html)
    records, seen = [], set()
    for link in parser.links:
        try:
            page = source_page(link['url'])
        except (ValueError, HTTPException):
            continue
        if not link['article'] and not urlparse(page).path.endswith(('-mid', '-midi')):
            continue
        title = ' '.join((link['label'] or link['text']).split())
        if not title or page in seen:
            continue
        seen.add(page)
        match = all_keywords(query, title)
        records.append({'id': 'bitmidi:' + urlparse(page).path[1:], 'title': title[:200], 'original_title': title[:255], 'source': 'BitMidi', 'creator': 'Unknown MIDI arranger (BitMidi)', 'page_url': page, 'midi_url': page, 'filename': title[:255], 'pdf_url': '', 'license': UNKNOWN_LICENSE, 'license_url': '', 'all_keywords_match': match, 'recommended': len(records) == 0 and match})
        if len(records) >= 12:
            break
    return records


def download_record(html, page):
    from .bitmidi import validate_url, UNKNOWN_LICENSE
    page = source_page(page)
    parser = Links(page)
    parser.feed(html)
    urls = set()
    for link in parser.links:
        try:
            parsed = validate_url(link['url'])
        except HTTPException:
            continue
        label = (link['label'] or link['text']).lower()
        if re.fullmatch(r'/uploads/[1-9][0-9]*\.mid', parsed.path) and not parsed.query and not parsed.fragment and (link['download'] or 'download' in label):
            urls.add('https://bitmidi.com' + parsed.path)
    if len(urls) != 1:
        raise ValueError('The BitMidi page did not provide one identifiable MIDI download link.')
    title = ' '.join(' '.join(parser.title_parts).split())
    return {'type': 'bitmidi', 'url': urls.pop(), 'page_url': page, 'title': title[:255], 'creator': 'Unknown MIDI arranger (BitMidi)', 'license': UNKNOWN_LICENSE, 'license_url': '', 'pdf_url': '', 'metadata_verified': True}
