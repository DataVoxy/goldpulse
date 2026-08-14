"""
Upload dashboard files to Cloudflare R2.
Called after generate_dashboard_data.py runs.

Required env vars (set in .env):
  CF_ACCOUNT_ID=your_cloudflare_account_id
  CF_R2_ACCESS_KEY=your_r2_access_key_id
  CF_R2_SECRET_KEY=your_r2_secret_access_key
  CF_R2_BUCKET=goldpulse-data
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
_env_path = Path(__file__).parent.parent / ".env"
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

# R2 endpoint
ENDPOINT = f"https://{ACCOUNT_ID}.r2.cloudflarestorage.com"

# Create S3 client (R2 is S3-compatible)
s3 = boto3.client(
    "s3",
    endpoint_url=ENDPOINT,
    aws_access_key_id=ACCESS_KEY,
    aws_secret_access_key=SECRET_KEY,
    config=Config(signature_version="s3v4"),
    region_name="auto",
)

LANDING_DIR = Path(__file__).parent.parent / "landing"

FILES = {
    "index.html": ("dashboard.html", "text/html; charset=utf-8"),
    "dashboard_data.json": ("dashboard_data.json", "application/json; charset=utf-8"),
    "sitemap.xml": ("sitemap_gold.xml", "application/xml; charset=utf-8"),
    "robots.txt": ("robots.txt", "text/plain; charset=utf-8"),
    "blog/free-gold-dashboard": ("blog_gold_dashboard.html", "text/html; charset=utf-8"),
    "blog/gold-algorithm-technical-breakdown": ("blog_gold_algorithm.html", "text/html; charset=utf-8"),
}

# SilverPulse files (uploaded to same bucket with silver/ prefix)
SILVER_FILES = {
    "silver/index.html": ("silverpulse_dashboard.html", "text/html; charset=utf-8"),
    "silver/silver_dashboard_data.json": ("silver_dashboard_data.json", "application/json; charset=utf-8"),
    "silver/sitemap.xml": ("sitemap_silver.xml", "application/xml; charset=utf-8"),
    "silver/robots.txt": ("robots.txt", "text/plain; charset=utf-8"),
}

# CryptoPulse files (uploaded with crypto/ prefix)
CRYPTO_FILES = {
    "crypto/index.html": ("cryptopulse_dashboard.html", "text/html; charset=utf-8"),
    "crypto/crypto_dashboard_data.json": ("crypto_dashboard_data.json", "application/json; charset=utf-8"),
    "crypto/sitemap.xml": ("sitemap_crypto.xml", "application/xml; charset=utf-8"),
    "crypto/robots.txt": ("robots.txt", "text/plain; charset=utf-8"),
}


def upload_file(local_name, r2_key, content_type):
    """Upload a single file to R2."""
    filepath = LANDING_DIR / local_name
    if not filepath.exists():
        print(f"  Skip {local_name} (not found)")
        return False

    s3.upload_file(
        str(filepath),
        BUCKET,
        r2_key,
        ExtraArgs={"ContentType": content_type},
    )
    print(f"  ✓ {local_name} → r2://{BUCKET}/{r2_key}")
    return True


def main():
    print(f"Uploading to R2 bucket: {BUCKET}")

    # Check if --silver flag is passed
    silver_only = "--silver" in sys.argv
    crypto_only = "--crypto" in sys.argv

    if not silver_only and not crypto_only:
        # Upload GoldPulse files
        print("  [GoldPulse]")
        for r2_key, (local_name, content_type) in FILES.items():
            upload_file(local_name, r2_key, content_type)

    if not crypto_only:
        # Upload SilverPulse files
        print("  [SilverPulse]")
        for r2_key, (local_name, content_type) in SILVER_FILES.items():
            upload_file(local_name, r2_key, content_type)

    if not silver_only:
        # Upload CryptoPulse files
        print("  [CryptoPulse]")
        for r2_key, (local_name, content_type) in CRYPTO_FILES.items():
            upload_file(local_name, r2_key, content_type)

    print("Done.")


if __name__ == "__main__":
    main()
