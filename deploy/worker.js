/**
 * GoldPulse Dashboard Worker
 * Serves index.html and dashboard_data.json from R2 bucket.
 * 
 * Setup:
 * 1. Create R2 bucket "goldpulse-data" in Cloudflare dashboard
 * 2. Create Worker, paste this code
 * 3. Bind R2 bucket as "BUCKET" in Worker settings
 * 4. Add custom domain: goldpulse.datavoxy.com
 */

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    let path = url.pathname;

    // Route to files
    if (path === '/' || path === '/index.html') {
      path = 'index.html';
    } else if (path === '/dashboard_data.json') {
      path = 'dashboard_data.json';
    } else {
      // Strip leading slash
      path = path.slice(1);
      // Strip trailing slash if present
      if (path.endsWith('/')) {
        path = path.slice(0, -1);
      }
    }

    // Try to get from R2
    const object = await env.BUCKET.get(path);

    if (!object) {
      return new Response('Not found', { status: 404 });
    }

    // Content types
    const contentTypes = {
      'html': 'text/html; charset=utf-8',
      'json': 'application/json; charset=utf-8',
      'xml': 'application/xml; charset=utf-8',
      'txt': 'text/plain; charset=utf-8',
      'png': 'image/png',
      'css': 'text/css',
      'js': 'application/javascript',
    };
    const ext = path.includes('.') ? path.split('.').pop() : null;
    // No extension = HTML (blog posts, pages)
    const contentType = ext ? (contentTypes[ext] || 'application/octet-stream') : 'text/html; charset=utf-8';

    // Cache: HTML = 1 hour, JSON = 1 min, blog posts = 1 hour
    const cacheControl = ext === 'json' ? 'public, max-age=60' : 'public, max-age=3600';

    return new Response(object.body, {
      headers: {
        'Content-Type': contentType,
        'Cache-Control': cacheControl,
        'Access-Control-Allow-Origin': '*',
      },
    });
  },
};
