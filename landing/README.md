# GoldPulse Landing Page

## Quick Start

1. **Generate track record:**
   ```
   py landing/generate_track_record.py
   ```

2. **Preview locally:**
   Open `landing/index.html` in your browser.

3. **Deploy:**
   Upload the `landing/` folder to any static host:
   - Vercel: `npx vercel landing/`
   - Netlify: drag & drop the folder
   - GitHub Pages: push to a repo and enable Pages
   - Cloudflare Pages: connect your repo

## Files

- `index.html` — Landing page (gold/dark theme, mobile responsive)
- `track_record.json` — Auto-generated trade data for the dashboard
- `generate_track_record.py` — Script to refresh track record from trade_results.csv

## Auto-Updates

The track record JSON is automatically regenerated every time a trade closes
(integrated into `core/strategy.py`). Just redeploy or use a CI/CD pipeline.

## Customization

- Edit pricing in `index.html` (search for "pricing-cards")
- Add your Telegram channel link to the CTA buttons
- Update the FAQ section as needed
- Track record updates automatically from your live trades
