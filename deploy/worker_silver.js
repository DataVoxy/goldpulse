/**
 * SilverPulse Dashboard Worker
 * Serves silverpulse dashboard from R2 bucket (silver/ prefix).
 * 
 * Setup:
 * 1. Use same R2 bucket "goldpulse-data"
 * 2. Create new Worker, paste this code
 * 3. Bind R2 bucket as "BUCKET" in Worker settings
 * 4. Add custom domain: silverpulse.datavoxy.com
 */

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    let path = url.pathname;

    // Route to files (stored under silver/ prefix in R2)
    if (path === '/' || path === '/index.html') {
      path = 'silver/index.html';
    } else if (path === '/silver_dashboard_data.json') {
      path = 'silver/silver_dashboard_data.json';
    } else {
      path = 'silver/' + path.slice(1);
    }

    const object = await env.BUCKET.get(path);

    if (!object) {
      return new Response('Not found', { status: 404 });
    }

    const contentTypes = {
      'html': 'text/html; charset=utf-8',
      'json': 'application/json; charset=utf-8',
      'png': 'image/png',
      'css': 'text/css',
      'js': 'application/javascript',
    };
    const ext = path.split('.').pop();
    const contentType = contentTypes[ext] || 'application/octet-stream';
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
