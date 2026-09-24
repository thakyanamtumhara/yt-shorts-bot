"""Separate blog illustrations from assets approved for standalone social posts."""

import hashlib
from urllib.parse import urlsplit, urlunsplit


def image_identity(url):
    if not isinstance(url, str):
        return None
    parts = urlsplit(url.strip())
    if parts.scheme not in ('https', 'http') or not parts.netloc:
        return None
    path = parts.path
    if path.lower().endswith(('.webp', '.jpg', '.jpeg')):
        path = path.rsplit('.', 1)[0]
    return urlunsplit((parts.scheme, parts.netloc.lower(), path, '', ''))


def blog_image_metadata(images, slug, base_url):
    assets = []
    for body, filename in images or []:
        if not body:
            continue
        if filename == 'reel-cover.webp':
            origin = 'reel_cover'
        elif filename in ('hero.webp', 'img1.webp', 'img2.webp'):
            origin = 'independent_generation'
        else:
            origin = 'unknown'
        assets.append({'url': f'{base_url}/p/{slug}-{filename}', 'origin': origin,
                       'sha256': hashlib.sha256(body).hexdigest()})
    return {'image_assets': assets, 'hero_image': assets[0]['url'] if assets else None}


def standalone_image_urls(urls, assets):
    """Require known independent origins; old or conflicting evidence holds closed."""
    approved = set()
    denied = set()
    fingerprints = {}
    denied_fingerprints = set()
    for asset in assets or []:
        if not isinstance(asset, dict):
            continue
        identity = image_identity(asset.get('url'))
        if not identity:
            continue
        origin = asset.get('origin')
        fingerprint = asset.get('sha256')
        if isinstance(fingerprint, str) and len(fingerprint) == 64:
            fingerprints[identity] = fingerprint
            if origin == 'reel_cover':
                denied_fingerprints.add(fingerprint)
        if origin == 'independent_generation' or (
                origin == 'curated_photo' and asset.get('approved') is True):
            approved.add(identity)
        else:
            denied.add(identity)
    eligible = []
    seen = set()
    seen_fingerprints = set()
    for url in urls or []:
        identity = image_identity(url)
        fingerprint = fingerprints.get(identity)
        if (not identity or identity in denied or identity not in approved
                or identity in seen or 'reel-cover' in urlsplit(url).path.lower()
                or fingerprint in denied_fingerprints
                or (fingerprint and fingerprint in seen_fingerprints)):
            continue
        seen.add(identity)
        if fingerprint:
            seen_fingerprints.add(fingerprint)
        eligible.append(url)
    return eligible
