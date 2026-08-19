/**
 * De/Het Trainer Worker
 * Serves dehet app from R2 bucket (dehet/ prefix).
 * 
 * Setup:
 * 1. Use same R2 bucket "goldpulse-data"
 * 2. Create new Worker, paste this code
 * 3. Bind R2 bucket as "BUCKET" in Worker settings
 * 4. Add custom domain: dehet.datavoxy.com
 */

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    let path = url.pathname;

    if (path === '/' || path === '/index.html') {
      path = 'dehet/index.html';
    } else {
      path = 'dehet/' + path.slice(1);
      if (path.endsWith('/')) path = path.slice(0, -1);
    }

    const object = await env.BUCKET.get(path);

    if (!object) {
      return new Response('Not found', { status: 404 });
    }

    const contentTypes = {
      'html': 'text/html; charset=utf-8',
      'json': 'application/json; charset=utf-8',
      'xml': 'application/xml; charset=utf-8',
      'txt': 'text/plain; charset=utf-8',
      'css': 'text/css',
      'js': 'application/javascript',
    };
    const ext = path.includes('.') ? path.split('.').pop() : null;
    const contentType = ext ? (contentTypes[ext] || 'application/octet-stream') : 'text/html; charset=utf-8';
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
