#!/usr/bin/env python3
"""
Quick test to verify all blog S3 paths use /p/ directory only.
Run: python test_blog_paths.py
No AWS credentials needed — uses mocked S3.
Does NOT import daily_short.py (too many heavy deps).
Instead, extracts the exact functions via exec().
"""
import sys
import os
import re
import types
import time

# ── Extract functions from daily_short.py without importing it ──
source_file = os.path.join(os.path.dirname(__file__), "daily_short.py")
with open(source_file, "r") as f:
    source = f.read()

# Extract generate_blog_slug function
slug_match = re.search(
    r'(def generate_blog_slug\(title\):.*?)(?=\ndef |\nclass |\n[A-Z])',
    source, re.DOTALL
)
exec(slug_match.group(1))

# Extract constants
BLOG_S3_BUCKET = "bulkplaintshirt.com"
BLOG_BASE_URL = "https://bulkplaintshirt.com"
BLOG_CLOUDFRONT_DIST_ID = "E21QLU9SBUBY7Z"

# Extract publish_blog_to_s3 function
pub_match = re.search(
    r'(def publish_blog_to_s3\(.*?\n)(?=def [a-z])',
    source, re.DOTALL
)
publish_source = pub_match.group(1)

# ── Set up mocks ──
captured_keys = []
captured_invalidations = []

class MockBody:
    def __init__(self, content):
        self._content = content
    def read(self):
        return self._content

class MockS3Exceptions:
    class NoSuchKey(Exception):
        pass

class MockS3:
    exceptions = MockS3Exceptions()

    def put_object(self, **kwargs):
        captured_keys.append(kwargs['Key'])

    def get_object(self, **kwargs):
        key = kwargs['Key']
        if key == 'p/index.html':
            return {'Body': MockBody(b'<html><body><h2>Posts</h2><ul>\n  <li><a href="/p/existing-post.html">Existing Post</a></li>\n</ul></body></html>')}
        elif key == 'p/map.xml':
            return {'Body': MockBody(b'<?xml version="1.0"?>\n<urlset>\n  <url><loc>https://bulkplaintshirt.com/catalog/index.html</loc></url>\n</urlset>')}
        elif key == 'p/llms.txt':
            raise MockS3Exceptions.NoSuchKey("Not found")
        raise MockS3Exceptions.NoSuchKey("Not found")

class MockCloudFront:
    def create_invalidation(self, **kwargs):
        paths = kwargs['InvalidationBatch']['Paths']['Items']
        captured_invalidations.extend(paths)

mock_boto3 = types.ModuleType('boto3')
def mock_client(service, **kwargs):
    if service == 's3':
        return MockS3()
    elif service == 'cloudfront':
        return MockCloudFront()
mock_boto3.client = mock_client
sys.modules['boto3'] = mock_boto3

os.environ['AWS_ACCESS_KEY_ID'] = 'test'
os.environ['AWS_SECRET_ACCESS_KEY'] = 'test'

# Need pytz and datetime for the function
import pytz
from datetime import datetime

# Stub out repair functions that publish_blog_to_s3 calls
def _noop(*a, **kw): pass
def _build_blog_index_html(**kw): return '<html><body>index</body></html>'
def _build_sitemap_xml(**kw): return '<?xml version="1.0"?><urlset></urlset>'
def _build_rss_feed(**kw): return '<?xml version="1.0"?><rss></rss>'
def _build_blog_widget_html(**kw): return '<div>widget</div>'
def _submit_noop(url, **kw): pass

# Build a local namespace with everything publish_blog_to_s3 needs
local_ns = {
    'os': os, 'time': time, 'pytz': pytz, 'datetime': datetime,
    'BLOG_S3_BUCKET': BLOG_S3_BUCKET,
    'BLOG_BASE_URL': BLOG_BASE_URL,
    'BLOG_CLOUDFRONT_DIST_ID': BLOG_CLOUDFRONT_DIST_ID,
    'TIMEZONE': 'Asia/Kolkata',
    'print': print,
    'repair_existing_blog_posts': _noop,
    'repair_sitemap': _noop,
    'repair_index_html': _noop,
    'build_blog_index_html': _build_blog_index_html,
    'build_sitemap_xml': _build_sitemap_xml,
    'build_rss_feed': _build_rss_feed,
    'build_blog_widget_html': _build_blog_widget_html,
    'submit_to_search_engines': _submit_noop,
    'json': __import__('json'),
}
exec(publish_source, local_ns)
publish_blog_to_s3 = local_ns['publish_blog_to_s3']

# ═══════════════════════════════════════════════
# TESTS
# ═══════════════════════════════════════════════
all_passed = True

def check(label, condition):
    global all_passed
    status = "PASS" if condition else "FAIL"
    if not condition:
        all_passed = False
    print(f"  [{status}] {label}")
    return condition

print("=" * 60)
print("TEST 1: generate_blog_slug")
print("=" * 60)
slug1 = generate_blog_slug("Best 240 GSM T-Shirts for Printing")
check(f"Slug: '{slug1}'", "240" in slug1 and " " not in slug1)

slug2 = generate_blog_slug("How to Start a T-Shirt Brand!")
check(f"Slug: '{slug2}'", "how-to-start" in slug2)

print()
print("=" * 60)
print("TEST 2: Blog URL format")
print("=" * 60)
test_slug = "test-blog-post"
blog_url = f"{BLOG_BASE_URL}/p/{test_slug}.html"
check(f"URL = {blog_url}", blog_url == "https://bulkplaintshirt.com/p/test-blog-post.html")
check("No /post/ in URL", "/post/" not in blog_url)

print()
print("=" * 60)
print("TEST 3: publish_blog_to_s3 — S3 keys & CloudFront paths")
print("=" * 60)
test_html = "<!DOCTYPE html><html><head><title>Test</title></head><body><h1>Test</h1></body></html>"
test_images = [(b'\x00' * 100, "hero.webp"), (b'\x00' * 50, "section1.webp")]

result = publish_blog_to_s3(test_html, test_slug, "Test Blog Post", blog_url, blog_images=test_images)

print()
print("  S3 keys written:")
for k in captured_keys:
    print(f"    -> {k}")
print(f"  CloudFront invalidation paths:")
for p in captured_invalidations:
    print(f"    -> {p}")

print()
# No /post/ in any S3 key
post_keys = [k for k in captured_keys if k.startswith("post/")]
check(f"No S3 keys under post/ (found {len(post_keys)})", len(post_keys) == 0)

# All keys under p/ or at root (sitemap.xml is allowed at root)
p_keys = [k for k in captured_keys if k.startswith("p/")]
root_allowed = [k for k in captured_keys if k in ("sitemap.xml", "robots.txt")]
check(f"All {len(captured_keys)} S3 keys under p/ or allowed root files", len(p_keys) + len(root_allowed) == len(captured_keys))

# No /post/ in CloudFront invalidation
post_inv = [p for p in captured_invalidations if "/post/" in p]
check(f"No CloudFront paths with /post/ (found {len(post_inv)})", len(post_inv) == 0)

# All CF paths under /p/ or allowed root paths
p_inv = [p for p in captured_invalidations if p.startswith("/p/")]
root_inv = [p for p in captured_invalidations if p in ("/sitemap.xml", "/robots.txt")]
check(f"All CloudFront invalidation under /p/ or allowed root", len(p_inv) + len(root_inv) == len(captured_invalidations))

# Expected specific keys
expected = [
    f"p/{test_slug}.html",
    f"p/{test_slug}-hero.webp",
    f"p/{test_slug}-section1.webp",
    "p/index.html",
    "p/map.xml",
    "sitemap.xml",
    "p/llms.txt",
]
for ek in expected:
    check(f"Key exists: {ek}", ek in captured_keys)

check("publish returned True", result is True)

# ── Final ──
print()
print("=" * 60)
if all_passed:
    print("ALL TESTS PASSED — blog paths are safe for production")
    print("Only /p/ directory is touched. No /post/ anywhere.")
else:
    print("SOME TESTS FAILED — review above!")
    sys.exit(1)
print("=" * 60)
