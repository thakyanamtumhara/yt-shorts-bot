#!/usr/bin/env python3
"""
Rebuild blog index.html + sitemap + RSS and publish to S3.

Usage:
    python rebuild_index.py          # rebuild & upload all
    python rebuild_index.py --dry    # build locally, don't upload (preview)

Requires env vars (for upload):
    AWS_ACCESS_KEY_ID
    AWS_SECRET_ACCESS_KEY
"""
import sys
import boto3

# daily_short.py has heavy module-level imports (moviepy etc.)
# They're installed via requirements.txt in the workflow.
from daily_short import (
    build_blog_index_html,
    build_sitemap_xml,
    build_rss_feed,
    BLOG_S3_BUCKET,
    BLOG_CLOUDFRONT_DIST_ID,
)

DRY_RUN = "--dry" in sys.argv


def main():
    print("=" * 60)
    print("🔄 Rebuilding blog index, sitemap & RSS")
    print("=" * 60)

    # ── Build ──
    print("\n📄 Building index.html...")
    index_html = build_blog_index_html()
    print(f"   ✅ index.html built ({len(index_html):,} bytes)")

    print("📄 Building sitemap (map.xml)...")
    sitemap_xml = build_sitemap_xml()
    print(f"   ✅ map.xml built ({len(sitemap_xml):,} bytes)")

    print("📄 Building RSS feed (feed.xml)...")
    rss_xml = build_rss_feed()
    print(f"   ✅ feed.xml built ({len(rss_xml):,} bytes)")

    if DRY_RUN:
        print("\n🏁 DRY RUN — skipping S3 upload")
        # Save locally for preview
        for name, content in [("index.html", index_html), ("map.xml", sitemap_xml), ("feed.xml", rss_xml)]:
            with open(f"/tmp/{name}", "w") as f:
                f.write(content)
            print(f"   📁 Saved /tmp/{name}")
        return

    # ── Upload to S3 ──
    print("\n☁️  Uploading to S3...")
    s3 = boto3.client("s3")

    files = [
        ("p/index.html", index_html, "text/html; charset=utf-8"),
        ("p/map.xml", sitemap_xml, "application/xml; charset=utf-8"),
        ("sitemap.xml", sitemap_xml, "application/xml; charset=utf-8"),
        ("p/feed.xml", rss_xml, "application/rss+xml; charset=utf-8"),
    ]

    for key, content, content_type in files:
        s3.put_object(
            Bucket=BLOG_S3_BUCKET,
            Key=key,
            Body=content.encode("utf-8"),
            ContentType=content_type,
            CacheControl="no-cache",
        )
        print(f"   ✅ s3://{BLOG_S3_BUCKET}/{key}")

    # ── CloudFront invalidation ──
    print("\n🌐 Invalidating CloudFront cache...")
    try:
        cf = boto3.client("cloudfront")
        cf.create_invalidation(
            DistributionId=BLOG_CLOUDFRONT_DIST_ID,
            InvalidationBatch={
                "Paths": {
                    "Quantity": 4,
                    "Items": ["/p/index.html", "/p/map.xml", "/sitemap.xml", "/p/feed.xml"],
                },
                "CallerReference": f"rebuild-{__import__('time').time():.0f}",
            },
        )
        print("   ✅ CloudFront invalidation created")
    except Exception as e:
        print(f"   ⚠️ CloudFront invalidation failed: {e}")

    print("\n🏁 Done!")
    print("   🔗 https://www.bulkplaintshirt.com/p/index.html")


if __name__ == "__main__":
    main()
