// Wrapper so NSSM can launch `node serve_frontend.js` without path-quoting issues
process.argv.push(
  '--listen', '3001',
  '--single',  // SPA mode: serve index.html for all routes
  'dist'
);
require('C:/Users/29abh/AppData/Roaming/npm/node_modules/serve/build/main.js');
