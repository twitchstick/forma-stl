"""Read explicit public model metadata, without guessing licenses or collecting page-wide images."""

import json
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit


def web_url(value, base=''):
    if not isinstance(value, str) or not value.strip():
        return ''
    try:
        url = urljoin(base, value.strip())
        p = urlsplit(url)
        if (
            p.scheme not in ('http', 'https')
            or not p.hostname
            or p.username
            or p.password
            or p.port not in (None, 80, 443)
        ):
            return ''
        return url[:4000]
    except ValueError:
        return ''


def source_fields(item):
    # Accept only known text fields; never retain arbitrary session/account data.
    images = item.get('image_urls', [])
    if not isinstance(images, list):
        images = []
    images = [item.get('image_url', '')] + images
    license = item.get('license', '')
    license = license if isinstance(license, str) else ''
    return {
        'license': license[:500],
        'license_url': web_url(item.get('license_url', '')),
        'attribution': str(item.get('attribution', ''))[:2000],
        'image_urls': list(dict.fromkeys(u for u in (web_url(v) for v in images) if u))[:20],
    }


class SourceParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.meta = {}
        self.images = []
        self.title = ''
        self.in_title = False
        self.json_ld = []
        self.script = None
        self.license_url = ''

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'meta':
            k = (a.get('property') or a.get('name') or '').lower()
            v = a.get('content', '')
            self.meta.setdefault(k, v)
            if k in ('og:image', 'og:image:url', 'og:image:secure_url', 'twitter:image'):
                self.images.append(v)
        if tag == 'title':
            self.in_title = True
        if tag == 'script' and a.get('type', '').lower() == 'application/ld+json':
            self.script = ''
        if tag in ('link', 'a') and 'license' in a.get('rel', '').lower().split():
            self.license_url = self.license_url or a.get('href', '')

    def handle_endtag(self, tag):
        if tag == 'title':
            self.in_title = False
        if tag == 'script' and self.script is not None:
            try:
                self.json_ld.append(json.loads(self.script))
            except (ValueError, RecursionError):
                pass
            self.script = None

    def handle_data(self, data):
        if self.in_title:
            self.title += data
        if self.script is not None:
            self.script += data


def parse_source(body, url):
    parser = SourceParser()
    parser.feed(body.decode('utf-8', errors='replace'))
    m = parser.meta
    result = {
        'title': m.get('og:title') or m.get('twitter:title') or parser.title.strip(),
        'creator': m.get('author', ''),
        'description': m.get('og:description') or m.get('description', ''),
        'tags': [t.strip() for t in m.get('keywords', '').split(',') if t.strip()][:100],
        'license': m.get('license', ''),
        'license_url': web_url(parser.license_url, url),
        'attribution': m.get('credittext', ''),
        'image_urls': [],
        'source_url': url,
    }
    images = list(parser.images)
    nodes = []
    for raw in parser.json_ld:
        for item in raw if isinstance(raw, list) else [raw]:
            if isinstance(item, dict):
                nodes.extend(item.get('@graph', []) if isinstance(item.get('@graph'), list) else [item])
    candidates = []
    for node in nodes[:100]:
        if not isinstance(node, dict):
            continue
        types = node.get('@type', [])
        types = [types] if isinstance(types, str) else types
        if isinstance(types, list) and set(types) & {'Product', 'CreativeWork', '3DModel', 'VisualArtwork', 'Article'}:
            candidates.append(node)
        if isinstance(node.get('mainEntity'), dict):
            candidates.insert(0, node['mainEntity'])

    def matches(node):
        value = node.get('url') or node.get('@id')
        return isinstance(value, str) and web_url(value, url).split('#')[0].rstrip('/') == url.split('#')[0].rstrip('/')

    matched = next((n for n in candidates if matches(n)), None)
    if matched is None:
        title = result['title'].lower()
        matched = next((n for n in candidates if isinstance(n.get('name'), str) and n['name'].lower() in title), None)
    for item in [matched] if matched is not None else candidates[:1]:
        if not isinstance(item, dict):
            continue
        types = item.get('@type', [])
        types = [types] if isinstance(types, str) else types
        if not isinstance(types, list) or not set(types) & {
            'Product',
            'CreativeWork',
            '3DModel',
            'VisualArtwork',
            'Article',
            'ImageObject',
        }:
            continue
        # Standalone ImageObjects on a page are not necessarily this model's photos.
        if types == ['ImageObject']:
            continue

        def name(value):
            if isinstance(value, list):
                return ', '.join(name(v) for v in value[:10])
            return str(value.get('name', '')) if isinstance(value, dict) else value if isinstance(value, str) else ''

        result['creator'] = result['creator'] or name(item.get('author') or item.get('creator'))
        result['description'] = result['description'] or name(item.get('description'))
        result['attribution'] = result['attribution'] or name(item.get('creditText') or item.get('copyrightNotice'))
        license = item.get('license')
        if isinstance(license, dict):
            result['license'] = result['license'] or name(license)
            result['license_url'] = result['license_url'] or web_url(license.get('url') or license.get('@id'), url)
        elif isinstance(license, str):
            link = web_url(license, url) if license.startswith(('http:', 'https:', '/')) else ''
            if link:
                result['license_url'] = result['license_url'] or link
            else:
                result['license'] = result['license'] or license
        values = item.get('image', [])
        values = values if isinstance(values, list) else [values]
        for value in values[:20]:
            images.append(value.get('contentUrl') or value.get('url', '') if isinstance(value, dict) else value)
        if not result['tags']:
            tags = item.get('keywords', [])
            tags = tags.split(',') if isinstance(tags, str) else tags
            if isinstance(tags, list):
                result['tags'] = [t.strip()[:80] for t in tags if isinstance(t, str) and t.strip()][:100]
    result['image_urls'] = list(dict.fromkeys(u for u in (web_url(v, url) for v in images) if u))[:20]
    result['image_url'] = next(iter(result['image_urls']), '')
    return result
