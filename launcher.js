// Tiny always-on launcher. Run: node launcher.js   (opens on port 3001)
// From your phone: Start / Stop / Restart the main bot server (server.js, port 3000).
const http = require('http');
const os = require('os');
const { spawn, exec } = require('child_process');

const LAUNCHER_PORT = 3001;
const BOT_PORT = 3000;
const AUTOSTART = true;   // start the bot server as soon as the launcher starts

function botUp() {
  return new Promise(resolve => {
    const r = http.get({ host: '127.0.0.1', port: BOT_PORT, path: '/', timeout: 1500 }, res => { res.resume(); resolve(true); });
    r.on('error', () => resolve(false));
    r.on('timeout', () => { r.destroy(); resolve(false); });
  });
}

function startBot() {
  const p = spawn(process.execPath, ['server.js'], { cwd: __dirname, detached: true, stdio: 'ignore', windowsHide: true });
  p.unref();
}

function stopBot() {
  return new Promise(resolve => {
    if (process.platform === 'win32') {
      exec('netstat -ano -p tcp', (err, out) => {
        const pids = new Set();
        (out || '').split('\n').forEach(l => {
          if (l.includes(':' + BOT_PORT + ' ') && /LISTENING/i.test(l)) pids.add(l.trim().split(/\s+/).pop());
        });
        if (!pids.size) return resolve();
        let n = pids.size;
        pids.forEach(pid => exec(`taskkill /PID ${pid} /T /F`, () => { if (--n === 0) resolve(); }));
      });
    } else {
      exec(`fuser -k ${BOT_PORT}/tcp`, () => resolve());
    }
  });
}

const sleep = ms => new Promise(r => setTimeout(r, ms));

// ---- installable home-screen app (PWA): icon + manifest + a tiny service worker, all inside this one file ----
const ICON_192 = Buffer.from('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', 'base64');
const ICON_512 = Buffer.from('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', 'base64');
const MANIFEST = JSON.stringify({
  name: 'Affiliate Bot Launcher', short_name: 'Bot Launcher', description: 'Start, stop and open the Affiliate Bot',
  start_url: '/', scope: '/', display: 'standalone', orientation: 'portrait',
  background_color: '#111111', theme_color: '#2563eb',
  icons: [
    { src: '/icon-192.png', sizes: '192x192', type: 'image/png', purpose: 'any maskable' },
    { src: '/icon-512.png', sizes: '512x512', type: 'image/png', purpose: 'any maskable' },
  ],
});
const SW = "self.addEventListener('install',()=>self.skipWaiting());self.addEventListener('activate',e=>e.waitUntil(self.clients.claim()));" +
           "self.addEventListener('fetch',e=>{});";   // the app always talks to the live launcher, nothing is cached

const PAGE = `<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Bot Launcher</title><link rel="manifest" href="/manifest.webmanifest"><meta name="theme-color" content="#2563eb"><link rel="icon" href="/icon-192.png"><link rel="apple-touch-icon" href="/icon-192.png"><meta name="mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-capable" content="yes"><style>
:root{--bg:#f5f5f7;--card:#fff;--text:#1d1d1f;--border:#d2d2d7}
@media(prefers-color-scheme:dark){:root{--bg:#111;--card:#1c1c1e;--text:#f2f2f2;--border:#3a3a3c}}
body{margin:0;background:var(--bg);color:var(--text);font-family:system-ui,sans-serif}
.w{max-width:480px;margin:0 auto;padding:16px}.c{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:16px;margin-bottom:16px}
button{width:100%;padding:14px;margin-top:10px;font-size:16px;border:0;border-radius:8px;color:#fff;background:#2563eb}
.r{background:#dc2626}.o{background:#f59e0b;color:#000}.dot{display:inline-block;width:12px;height:12px;border-radius:50%;margin-right:8px;background:#9ca3af}
a.open{display:block;text-align:center;padding:14px;margin-top:10px;border-radius:8px;background:#16a34a;color:#fff;text-decoration:none;font-size:16px}
</style></head><body><div class="w"><h2>Bot Launcher</h2><div class="c">
<div><span class="dot" id="d"></span><strong id="s">checking…</strong></div>
<a class="open" id="o" href="#" target="_blank" rel="noopener" onclick="var w=window.open(this.href,'_blank');return !w;" style="display:none">Open Affiliate Bot</a>
<button onclick="act('start')">▶ Start bot server</button>
<button class="o" onclick="act('restart')">↻ Restart</button>
<button class="r" onclick="act('stop')">■ Stop</button>
<div id="m" style="margin-top:10px;font-size:14px"></div></div><div style="font-size:11px;opacity:.5;text-align:center">launcher v3 (installable app, opens bot in new tab)</div></div>
<script>
const base = location.protocol + '//' + location.hostname + ':${BOT_PORT}';
document.getElementById('o').href = base;
async function refresh(){try{const j=await (await fetch('/status')).json();
 document.getElementById('s').textContent=j.up?'Bot server is RUNNING':'Bot server is STOPPED';
 document.getElementById('d').style.background=j.up?'#16a34a':'#dc2626';
 document.getElementById('o').style.display=j.up?'block':'none';}catch(e){}}
async function act(a){document.getElementById('m').textContent='Please wait…';
 const t=await (await fetch('/'+a,{method:'POST'})).text();document.getElementById('m').textContent=t;refresh();}
refresh();setInterval(refresh,3000);
if('serviceWorker' in navigator){navigator.serviceWorker.register('/sw.js').catch(function(){});}
</script></body></html>`;

http.createServer(async (req, res) => {
  const send = (code, text, type = 'text/plain; charset=utf-8') => { res.writeHead(code, { 'Content-Type': type }); res.end(text); };
  if (req.method === 'GET' && req.url === '/') return send(200, PAGE, 'text/html; charset=utf-8');
  if (req.method === 'GET' && req.url === '/manifest.webmanifest') return send(200, MANIFEST, 'application/manifest+json');
  if (req.method === 'GET' && req.url === '/sw.js') { res.writeHead(200, { 'Content-Type': 'text/javascript', 'Service-Worker-Allowed': '/' }); return res.end(SW); }
  if (req.method === 'GET' && (req.url === '/icon-192.png' || req.url === '/icon-512.png')) {
    res.writeHead(200, { 'Content-Type': 'image/png', 'Cache-Control': 'public, max-age=86400' });
    return res.end(req.url === '/icon-192.png' ? ICON_192 : ICON_512);
  }
  if (req.method === 'GET' && req.url === '/status') return send(200, JSON.stringify({ up: await botUp() }), 'application/json');
  if (req.method === 'POST' && req.url === '/start') {
    if (await botUp()) return send(200, 'Already running.');
    startBot(); await sleep(2000);
    return send(200, (await botUp()) ? 'Started.' : 'Starting… give it a few seconds.');
  }
  if (req.method === 'POST' && req.url === '/stop') { await stopBot(); await sleep(500); return send(200, 'Stopped.'); }
  if (req.method === 'POST' && req.url === '/restart') {
    await stopBot(); await sleep(1000); startBot(); await sleep(2000);
    return send(200, (await botUp()) ? 'Restarted.' : 'Restarting… give it a few seconds.');
  }
  send(404, 'Not found');
}).listen(LAUNCHER_PORT, '0.0.0.0', async () => {
  console.log('Bot Launcher running.');
  Object.values(os.networkInterfaces()).flat().filter(i => i.family === 'IPv4' && !i.internal)
    .forEach(i => console.log(`  Phone (same WiFi): http://${i.address}:${LAUNCHER_PORT}`));
  if (AUTOSTART && !(await botUp())) { startBot(); console.log('Bot server started.'); }
});
