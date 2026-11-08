"""Provider identity validation for browser-visible catalog imports."""

import hashlib
import re
from urllib.parse import urlsplit, unquote
from source_metadata import web_url

PROVIDERS = {'printables': 'printables.com', 'cults3d': 'cults3d.com', 'thangs': 'thangs.com'}


def identity(value, provider):
    p = urlsplit(str(value))
    if (
        provider not in PROVIDERS
        or p.scheme != 'https'
        or p.username
        or p.password
        or p.port not in (None, 443)
        or p.hostname not in (PROVIDERS[provider], 'www.' + PROVIDERS[provider])
    ):
        raise ValueError('Choose a model URL from the selected website.')
    path = p.path.rstrip('/')
    if provider == 'printables':
        match = re.search(r'^/(?:[a-z]{2}/)?model/(\d+)(?:-[^/]*)?(?:/.*)?$', path)
        if not match:
            raise ValueError('Choose a Printables model page.')
        key = match[1]
        path = '/model/' + key
    elif provider == 'cults3d':
        match = re.fullmatch(r'/[a-z]{2}/3d-model/[^/]+/[^/]+', path)
        if not match:
            raise ValueError('Choose a Cults3D model page.')
        # Language variants represent the same model.
        path = '/en' + path[3:]
        key = hashlib.sha256(unquote(path).encode()).hexdigest()[:32]
    else:
        match = re.search(r'/3d-model/([^/]+)$', path) or re.fullmatch(r'/m/(\d+)', path)
        if not match:
            raise ValueError('Choose a Thangs model page.')
        numeric = re.search(r'(\d+)$', match[1])
        if not numeric:
            raise ValueError('Thangs model URL needs its model number.')
        key = numeric[1]
        # Short and full designer URLs share an identity.
    return provider + ':' + key, 'https://' + PROVIDERS[provider] + path


def normalize_website_item(item):
    provider = item.get('provider')
    remote_id, source = identity(item.get('source_url', ''), provider)
    title = str(item.get('title', '')).strip()[:300]
    if not title:
        raise ValueError('Each model needs a title.')
    memberships = item.get('collections', [])
    if not isinstance(memberships, list) or len(memberships) > 100:
        raise ValueError('Too many collection memberships.')
    collections = {}
    for entry in memberships:
        if not isinstance(entry, dict):
            raise ValueError('Invalid collection.')
        raw = str(entry.get('id', '')).strip()
        name = str(entry.get('name', '')).strip()[:100]
        if not raw or not name:
            raise ValueError('Each collection needs a name and ID.')
        key = (
            raw
            if re.fullmatch(provider + r':[a-f0-9]{32}', raw)
            else provider + ':' + hashlib.sha256(raw.encode()).hexdigest()[:32]
        )
        collections[key] = {
            'id': key,
            'name': name,
            'source_url': web_url(entry.get('source_url') or raw.split('#')[0]),
        }
    tags = item.get('tags', [])
    if not isinstance(tags, list):
        raise ValueError('Tags must be a list.')
    return dict(
        id=remote_id,
        provider=provider,
        title=title,
        source_url=source,
        creator=str(item.get('creator', ''))[:300],
        description=str(item.get('description', ''))[:30000],
        tags=list(dict.fromkeys(str(t).strip()[:80] for t in tags if str(t).strip()))[:100],
        image_url=item.get('image_url', ''),
        profile_id='',
        favorite=bool(item.get('favorite', False)),
        collections=list(collections.values()),
    )
