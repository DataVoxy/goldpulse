"""
Push all blog posts to Cloudflare R2. Zero config.

Usage:
    py deploy/push_blogs.py              # Upload all blogs
    py deploy/push_blogs.py --datavoxy   # Upload datavoxy.com main site too

How it works:
    - Scans landing/ for files named blog_*.html
    - Converts filename to URL slug: blog_gold_algorithm.html → blog/gold-algorithm
    - Uploads to R2 bucket under the correct prefix

To add a new blog post:
    1. Create landing/blog_your_slug_here.html
    2. Run: py deploy/push_blogs.py
    That's it.
"""
import os
import sys
from pathlib import Path

try:
    import boto3
    from botocore.config import Config
except ImportError:
    print("Install boto3: pip install boto3")
    sys.exit(1)

# Load .env
ROOT = Path(__file__).parent.parent
_env_path = ROOT / ".env"
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

ACCOUNT_ID = os.environ.get("CF_ACCOUNT_ID", "")
ACCESS_KEY = os.environ.get("CF_R2_ACCESS_KEY", "")
SECRET_KEY = os.environ.get("CF_R2_SECRET_KEY", "")
BUCKET = os.environ.get("CF_R2_BUCKET", "goldpulse-data")

if not all([ACCOUNT_ID, ACCESS_KEY, SECRET_KEY]):
    print("Missing R2 credentials in .env")
    print("Need: CF_ACCOUNT_ID, CF_R2_ACCESS_KEY, CF_R2_SECRET_KEY")
    sys.exit(1)

ENDPOINT = f"https://{ACCOUNT_ID}.r2.cloudflarestorage.com"

s3 = boto3.client(
    "s3",
    endpoint_url=ENDPOINT,
    aws_access_key_id=ACCESS_KEY,
    aws_secret_access_key=SECRET_KEY,
    config=Config(signature_version="s3v4"),
    region_name="auto",
)

LANDING_DIR = ROOT / "landing"
DATAVOXY_DIR = ROOT / "datavoxy"


def filename_to_slug(filename):
    """
    Convert blog filename to URL slug.
    blog_gold_algorithm.html → gold-algorithm
    blog_gold_dashboard.html → gold-dashboard  (but we keep existing: free-gold-dashboard)
    """
    name = filename.replace("blog_", "").replace(".html", "")
    return name.replace("_", "-")


# Manual overrides for slugs that don't follow the convention
SLUG_OVERRIDES = {
    "blog_gold_dashboard.html": "free-gold-dashboard",
    "blog_gold_algorithm.html": "gold-algorithm-technical-breakdown",
    "blog_first_100_signals.html": "first-100-signals",
    "blog_why_we_automate.html": "why-we-automate-everything",
}


def push_blogs():
    """Find all blog_*.html files and upload them."""
    blog_files = sorted(LANDING_DIR.glob("blog_*.html"))

    if not blog_files:
        print("No blog files found in landing/")
        return

    print(f"Found {len(blog_files)} blog post(s)")
    print(f"Uploading to R2 bucket: {BUCKET}\n")

    for filepath in blog_files:
        filename = filepath.name
        slug = SLUG_OVERRIDES.get(filename, filename_to_slug(filename))
        r2_key = f"blog/{slug}"

        s3.upload_file(
            str(filepath),
            BUCKET,
            r2_key,
            ExtraArgs={"ContentType": "text/html; charset=utf-8"},
        )
        print(f"  ✓ {filename} → goldpulse.datavoxy.com/{r2_key}")

    print(f"\nDone. {len(blog_files)} blog(s) deployed.")


def push_datavoxy():
    """Upload datavoxy.com main site."""
    index_path = DATAVOXY_DIR / "index.html"
    if not index_path.exists():
        print("datavoxy/index.html not found")
        return

    # datavoxy.com is served from a different prefix or worker
    # For now upload to datavoxy/ prefix in same bucket
    s3.upload_file(
        str(index_path),
        BUCKET,
        "datavoxy/index.html",
        ExtraArgs={"ContentType": "text/html; charset=utf-8"},
    )
    print("  ✓ datavoxy/index.html → datavoxy.datavoxy.com/")


def main():
    push_blogs()

    if "--datavoxy" in sys.argv:
        print("\n[DataVoxy main site]")
        push_datavoxy()


if __name__ == "__main__":
    main()
