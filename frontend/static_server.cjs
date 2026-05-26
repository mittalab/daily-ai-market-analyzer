/**
 * Minimal SPA static file server — no external deps, CJS.
 * Serves frontend/dist on port 3001.
 * Unknown routes → index.html (client-side routing support).
 */
const http = require('http');
const fs   = require('fs');
const path = require('path');

const PORT    = 3001;
const DIST    = path.join(__dirname, 'dist');

const MIME = {
  '.html': 'text/html',
  '.js':   'application/javascript',
  '.css':  'text/css',
  '.json': 'application/json',
  '.png':  'image/png',
  '.jpg':  'image/jpeg',
  '.svg':  'image/svg+xml',
  '.ico':  'image/x-icon',
  '.woff2':'font/woff2',
};

http.createServer((req, res) => {
  const urlPath = req.url.split('?')[0];
  let filePath  = path.join(DIST, urlPath);

  // Default to index.html for directories
  if (fs.existsSync(filePath) && fs.statSync(filePath).isDirectory()) {
    filePath = path.join(filePath, 'index.html');
  }

  // SPA fallback: unknown file → index.html
  if (!fs.existsSync(filePath)) {
    filePath = path.join(DIST, 'index.html');
  }

  const ext  = path.extname(filePath);
  const mime = MIME[ext] || 'application/octet-stream';

  fs.readFile(filePath, (err, data) => {
    if (err) {
      res.writeHead(500);
      res.end('Server error');
      return;
    }
    res.writeHead(200, { 'Content-Type': mime });
    res.end(data);
  });
}).listen(PORT, () => {
  console.log(`Frontend serving ${DIST} on port ${PORT}`);
});
