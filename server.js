// Affiliate Bot control server. Run: node server.js
const http = require('http');
const fs = require('fs');
const path = require('path');
const os = require('os');
const { spawn, execFile } = require('child_process');
const net = require('net');

// Makes adb's phone discovery (mdns) work reliably on Windows. Inherited by every adb command below.
process.env.ADB_MDNS_OPENSCREEN = '1';
let adbRestarted = false;   // adb is restarted once per server start so the setting above takes effect

const PORT = 3000;
const WORKDIR = __dirname;   // server.js now lives in the same folder as main_link.py

// ---- .env update: edit these 3 lines to match your setup ----
const ENV_FILE = path.join(__dirname, '.env');   // the .env next to main_link.py
const ENV_KEY = 'ANDROID_SERIAL';   // exact variable name your script reads
const ENV_USE_PORT = true;     // true = write ip:port, false = write only the ip
// -------------------------------------------------------------

// Update (or add) one KEY=value line in .env, leaving all other lines untouched
function updateEnv(value) {
  let text = '';
  try { text = fs.readFileSync(ENV_FILE, 'utf8'); } catch { log('.env not found, creating a new one'); }
  const eol = text.includes('\r\n') ? '\r\n' : '\n';
  const lines = text ? text.split(/\r?\n/) : [];
  const re = new RegExp('^\\s*' + ENV_KEY + '\\s*=');
  let found = false;
  const out = lines.map(l => (re.test(l) ? (found = true, `${ENV_KEY}=${value}`) : l));
  if (!found) {
    if (out.length && out[out.length - 1] === '') out.splice(out.length - 1, 0, `${ENV_KEY}=${value}`);
    else out.push(`${ENV_KEY}=${value}`);
  }
  fs.writeFileSync(ENV_FILE, out.join(eol));
  log(`.env updated: ${ENV_KEY}=${value}`);
}

let logs = [];          // all lines of the current job
let clients = [];       // SSE connections
let status = 'idle';    // idle | running | done | error
let appiumProc = null;
let currentProc = null;   // the command running right now (so Stop can kill it)
let stopRequested = false;
let jobPromise = null;
let lastParams = null;   // the form values of the last run (shown on every device, used by Start again)
let appiumLogs = [];   // Appium has its own log box (it keeps running between jobs, so it is not cleared per job)
let promptText = '';   // the YouTube Create prompt printed by main_link.py
let captionText = '';  // the Instagram caption printed by main_link.py (also before the video is made)
let phoneState = null; // what the bot does with the phone right now: {state: 'free'|'soon'|'busy', msg, seconds, at} (lines "PHONE>> ..." from phone_focus.py)

const CAPTION_FILE = path.join(WORKDIR, 'manual_caption.txt');   // the caption typed on the page for "Only post a Reel" (main_link.py reads it)
const FEEDBACK_FILE = path.join(WORKDIR, 'live_feedback.json');   // notes typed on the page; user_feedback.py (in main_link.py) reads them
let feedbackItems = [];   // [{id, kind: 'video' | 'phone', text}] of the current run
let lastRunDir = '';      // output folder of the latest run (printed by user_feedback.py as "RUNDIR>> ...")
function writeFeedback() { try { fs.writeFileSync(FEEDBACK_FILE, JSON.stringify({ items: feedbackItems })); } catch (e) { console.log('could not write ' + FEEDBACK_FILE + ': ' + e.message); } }

// ============================== HISTORY ==============================
// Every run is saved in history.json (next to server.js), so the list survives restarts and is the same on every device.
// The pair PIN is never saved. Deleting an entry removes only the entry, never the video files.
const HISTORY_FILE = path.join(WORKDIR, 'history.json');
const HISTORY_MAX = 300;       // the oldest runs are dropped after this many
let history = [];              // newest first
let curHist = null;            // the entry of the run that is going on now
let histTail = [];             // the last lines of that run's log
let histTimer = null;

function saveHistoryNow() {
  clearTimeout(histTimer);
  const data = JSON.stringify({ version: 1, items: history });
  try { fs.writeFileSync(HISTORY_FILE + '.tmp', data); fs.renameSync(HISTORY_FILE + '.tmp', HISTORY_FILE); }
  catch (e) { try { fs.writeFileSync(HISTORY_FILE, data); } catch (e2) { console.log('could not save history: ' + e2.message); } }
}
function saveHistory() { clearTimeout(histTimer); histTimer = setTimeout(saveHistoryNow, 800); }
function loadHistory() {
  try { const j = JSON.parse(fs.readFileSync(HISTORY_FILE, 'utf8')); history = Array.isArray(j.items) ? j.items : []; } catch { history = []; }
  let changed = false;
  history.forEach(h => { if (h.status === 'running') { h.status = 'stopped'; h.endedAt = h.endedAt || Date.now(); h.error = h.error || 'Interrupted: the server was restarted.'; changed = true; } });
  if (changed) saveHistoryNow();
}
// "interactive-colorful-pom-pom-cat-toy/p/gqxtv5" -> "interactive colorful pom pom cat toy" (used until the real product name is printed)
function nameFromLink(link) {
  try {
    const u = new URL(link);
    const slug = u.pathname.split('/').filter(Boolean).find(s => s.length > 12 && s.includes('-')) || '';
    return slug ? decodeURIComponent(slug).replace(/[-_]+/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 120) : u.hostname;
  } catch { return ''; }
}
function startHistory(p, extra) {
  const reel = p.mode === 'reelpost';
  const link = reel ? '' : String(p.productLink || '');
  const e = {
    id: Date.now().toString(36) + Math.random().toString(36).slice(2, 5),
    startedAt: Date.now(), endedAt: null, status: 'running', mode: p.mode,
    productLink: link, name: reel ? '' : nameFromLink(link),
    params: { productLink: link, mode: p.mode, videoType: p.videoType, reelFormat: p.reelFormat || 'auto', customStyle: p.customStyle || '', audio: p.audio || 'auto',
              cta: !!p.cta, textOverlay: !!p.textOverlay, overlayOpts: p.overlayOpts || [], overlayCustom: p.overlayCustom || '', caption: reel ? String(p.caption || '') : '' },
    redo: !!(extra && extra.redo), feedback: (extra && extra.feedback) || '',
    prompt: '', caption: reel ? String(p.caption || '') : '', folder: '', error: '', logTail: [],
  };
  history.unshift(e);
  if (history.length > HISTORY_MAX) history = history.slice(0, HISTORY_MAX);
  curHist = e; histTail = [];
  saveHistoryNow();
}
// called for every log line of the running job
function histCapture(l) {
  const h = curHist; if (!h) return;
  histTail.push(l); if (histTail.length > 300) histTail.shift();
  let m;
  if (l.startsWith('RUNDIR>> ')) h.folder = l.slice(9).trim();
  else if (l.startsWith('YTPROMPT>> ')) h.prompt = l.slice(11);
  else if (l.startsWith('CAPTION>> ')) { try { h.caption = String(JSON.parse(l.slice(10))); } catch (e) {} }
  else if ((m = /^\s*Naam\s*:\s*(.+?)\s*$/.exec(l)) && !/^\(nahi mila\)$/.test(m[1])) h.name = m[1].slice(0, 200);
  else if (!h.folder && (m = /Folder:\s*(.+?)\s*$/.exec(l))) h.folder = m[1];
  if (/^❌ /.test(l.trim())) h.error = l.trim().slice(2).trim().slice(0, 300);
}
function finishHistory(s) {
  const h = curHist; if (!h) return;
  h.status = s; h.endedAt = Date.now(); h.logTail = histTail.slice(-200);
  if (s === 'error' && !h.error) h.error = (histTail.filter(x => x.trim()).pop() || 'Failed').slice(0, 300);
  if (s === 'stopped' && !h.error) h.error = 'Stopped by the user.';
  curHist = null; histTail = [];
  saveHistoryNow();
}
function folderPath(h) { return h.folder ? path.resolve(WORKDIR, h.folder) : ''; }
function histView(h) {      // what the page gets: the entry plus facts about the files, without the long log
  const dir = folderPath(h);
  const has = f => { try { return fs.statSync(path.join(dir, f)).isFile(); } catch { return false; } };
  let folderExists = false; try { folderExists = !!dir && fs.statSync(dir).isDirectory(); } catch {}
  const { logTail, ...rest } = h;
  return { ...rest, folderExists, hasVideo: folderExists && has('video.mp4'), posted: folderExists && has('posted.txt') };
}
function streamVideo(req, res, h, download) {
  const dir = folderPath(h);
  const file = dir ? path.join(dir, 'video.mp4') : '';
  let st; try { st = fs.statSync(file); } catch { res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' }); return res.end('The video file is not there (any more).'); }
  const size = st.size;
  const headers = { 'Content-Type': 'video/mp4', 'Accept-Ranges': 'bytes' };
  if (download) headers['Content-Disposition'] = `attachment; filename="reel_${h.id}.mp4"`;
  const m = /bytes=(\d*)-(\d*)/.exec(req.headers.range || '');
  if (m && (m[1] || m[2])) {      // phones play video in pieces, so the Range header must work
    let start = m[1] ? parseInt(m[1], 10) : size - parseInt(m[2], 10);
    let end = m[1] && m[2] ? parseInt(m[2], 10) : size - 1;
    start = Math.max(0, start); end = Math.min(size - 1, end);
    if (start > end || start >= size) { res.writeHead(416, { 'Content-Range': `bytes */${size}` }); return res.end(); }
    res.writeHead(206, { ...headers, 'Content-Range': `bytes ${start}-${end}/${size}`, 'Content-Length': end - start + 1 });
    return fs.createReadStream(file, { start, end }).on('error', () => res.destroy()).pipe(res);
  }
  res.writeHead(200, { ...headers, 'Content-Length': size });
  fs.createReadStream(file).on('error', () => res.destroy()).pipe(res);
}
// GET /history | DELETE /history | GET /history/export | GET /history/<id> | DELETE /history/<id> | GET /history/<id>/video | POST /history/<id>/open-folder
function handleHistory(req, res) {
  const u = new URL(req.url, 'http://x');
  const parts = u.pathname.split('/').filter(Boolean);
  if (parts[0] !== 'history') return false;
  const json = (code, obj, extra) => { res.writeHead(code, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store', ...(extra || {}) }); res.end(JSON.stringify(obj)); };
  const text = (code, t) => { res.writeHead(code, { 'Content-Type': 'text/plain; charset=utf-8' }); res.end(t); };
  const id = parts[1], action = parts[2];
  if (!id) {
    if (req.method === 'GET') { json(200, history.map(histView)); return true; }
    if (req.method === 'DELETE') {      // clear all, but never the run that is going on right now
      const keep = history.filter(h => h.status === 'running');
      const removed = history.length - keep.length;
      history = keep; saveHistoryNow();
      json(200, { removed }); return true;
    }
    text(405, 'Method not allowed'); return true;
  }
  if (id === 'export' && req.method === 'GET') {
    json(200, { exportedAt: new Date().toISOString(), items: history }, { 'Content-Disposition': 'attachment; filename="affiliate-history.json"' });
    return true;
  }
  if (!/^[a-z0-9]{4,20}$/.test(id)) { text(400, 'Bad id'); return true; }
  const h = history.find(x => x.id === id);
  if (!h) { text(404, 'That entry does not exist (any more).'); return true; }
  if (!action && req.method === 'GET') { json(200, { ...histView(h), logTail: h.logTail || [] }); return true; }
  if (!action && req.method === 'DELETE') {
    if (h.status === 'running') { text(409, 'This run is still in progress. Stop it first.'); return true; }
    history = history.filter(x => x !== h); saveHistoryNow();
    text(200, 'deleted'); return true;
  }
  if (action === 'video' && req.method === 'GET') { streamVideo(req, res, h, u.searchParams.has('download')); return true; }
  if (action === 'open-folder' && req.method === 'POST') {
    const mine = new Set(['127.0.0.1', '::1']);
    Object.values(os.networkInterfaces()).flat().forEach(i => mine.add(i.address));
    const ip = String(req.socket.remoteAddress || '').replace(/^::ffff:/, '');
    if (!mine.has(ip)) { text(403, 'Open this page on the PC itself to open the folder there.'); return true; }
    const dir = folderPath(h);
    try { if (!dir || !fs.statSync(dir).isDirectory()) throw 0; } catch { text(404, 'The folder is not there (any more).'); return true; }
    const opener = process.platform === 'win32' ? 'explorer' : process.platform === 'darwin' ? 'open' : 'xdg-open';
    try { spawn(opener, [dir], { detached: true, stdio: 'ignore' }).on('error', () => {}).unref(); } catch (e) {}
    text(200, 'opened'); return true;
  }
  text(404, 'Not found'); return true;
}
loadHistory();

function phonePayload() {   // 'soon' carries the seconds that are left, so every device can run its own countdown
  if (!phoneState) return null;
  const left = phoneState.state === 'soon' ? Math.max(0, Math.round(phoneState.seconds - (Date.now() - phoneState.at) / 1000)) : 0;
  return { state: phoneState.state, msg: phoneState.msg, left };
}
function sendPhone() { clients.forEach(c => c.write(`data: ${JSON.stringify({ phone: phonePayload() })}\n\n`)); }

function log(line) {
  const text = String(line).replace(/\r/g, '');
  text.split('\n').forEach(l => {
    if (!l.trim()) return;
    if (l.startsWith('PHONE>> ')) {      // "PHONE>> free|message", "PHONE>> soon|20|message", "PHONE>> busy|message"
      const parts = l.slice(8).split('|');
      phoneState = parts[0] === 'soon' ? { state: 'soon', seconds: Number(parts[1]) || 20, msg: parts[2] || '', at: Date.now() }
                                       : { state: parts[0], msg: parts[1] || '' };
      sendPhone();
      return;                            // this line is for the page banner only, not for the log
    }
    if (l.startsWith('RUNDIR>> ')) lastRunDir = l.slice(9).trim();
    if (l.startsWith('YTPROMPT>> ')) { promptText = l.slice(11); clients.forEach(c => c.write(`data: ${JSON.stringify({ prompt: promptText })}\n\n`)); }
    if (l.startsWith('CAPTION>> ')) {     // one JSON string (the caption has line breaks, a log line cannot)
      try { captionText = String(JSON.parse(l.slice(10))); clients.forEach(c => c.write(`data: ${JSON.stringify({ caption: captionText })}\n\n`)); } catch (e) {}
    }
    if (curHist) histCapture(l);
    logs.push(l);
    clients.forEach(c => c.write(`data: ${JSON.stringify({ line: l })}\n\n`));
  });
}
function setStatus(s) {
  status = s;
  if (s !== 'running') finishHistory(s);       // the run is over: save how it ended
  if (s !== 'running' && phoneState) { phoneState = null; sendPhone(); }    // the job is over: the phone is yours
  clients.forEach(c => c.write(`data: ${JSON.stringify({ status: s })}\n\n`));
}

// Run a command (no shell), stream output, resolve with {code, out}
function run(cmd, args, opts = {}) {
  return new Promise((resolve, reject) => {
    log(`\n$ ${cmd} ${args.join(' ')}`);
    let out = '';
    if (stopRequested) return reject(new Error('STOP'));
    const p = spawn(cmd, args, { cwd: opts.cwd, env: { ...process.env, ...(opts.env || {}) } });
    currentProc = p;
    p.stdout.on('data', d => { out += d; log(d); });
    p.stderr.on('data', d => { out += d; log(d); });
    p.on('error', e => { log('ERROR: ' + e.message); resolve({ code: -1, out }); });
    p.on('close', code => (stopRequested ? reject(new Error('STOP')) : resolve({ code, out })));
    if (opts.input) { p.stdin.write(opts.input + '\n'); }
    else { p.stdin.end(); }   // nothing will ever be typed, so input() in Python fails fast instead of hanging
    if (opts.timeout) setTimeout(() => { try { p.kill(); } catch {} }, opts.timeout);
  });
}

// Kill a command and everything it started (python -> adb, etc.)
function killProc(p) {
  if (!p || p.exitCode !== null || !p.pid) return;
  if (process.platform === 'win32') spawn('taskkill', ['/pid', String(p.pid), '/T', '/F']);
  else p.kill('SIGTERM');
}
async function stopJob() {
  if (status !== 'running') return;
  stopRequested = true;
  log('\n⏹ Stop requested...');
  killProc(currentProc);
  try { await jobPromise; } catch {}
}

function logAppium(chunk) {
  String(chunk).replace(/\r/g, '').split('\n').forEach(l => {
    if (!l.trim()) return;
    appiumLogs.push(l);
    if (appiumLogs.length > 2000) appiumLogs.shift();
    clients.forEach(c => c.write(`data: ${JSON.stringify({ appium: l })}\n\n`));
  });
}

function startAppium() {
  return new Promise(resolve => {
    if (appiumProc && !appiumProc.killed) { log('Appium already running, reusing it.'); return resolve(); }
    log('\n$ appium   (its output is in the Appium log box)');
    appiumProc = spawn('appium', [], { shell: true }); // appium is a .cmd on Windows
    const onData = d => { logAppium(d); if (/listener started|Appium REST http interface/i.test(String(d))) { log('Appium is ready.'); resolve(); } };
    appiumProc.stdout.on('data', onData);
    appiumProc.stderr.on('data', onData);
    appiumProc.on('close', () => { appiumProc = null; logAppium('Appium stopped.'); log('Appium stopped.'); });
    appiumProc.on('error', e => { logAppium('Appium could not start: ' + e.message); log('Appium could not start: ' + e.message); });
    const t = setInterval(() => { if (stopRequested) { clearInterval(t); resolve(); } }, 500);
    setTimeout(() => { clearInterval(t); resolve(); }, 15000); // don't wait forever
  });
}

const sleep = ms => new Promise(r => setTimeout(r, ms));

// Ask adb which phones are visible on the WiFi (mdns) and return "ip:port" for the given service type.
// type = '_adb-tls-connect' (normal connect address) or '_adb-tls-pairing' (only while the pairing popup is open)
// onlyIp = only accept the phone with this IP (so other phones on the WiFi are ignored)
function findService(type, tries = 3, onlyIp = null) {
  const once = () => new Promise(resolve => {
    execFile('adb', ['mdns', 'services'], { timeout: 10000 }, (err, stdout) => {
      const lines = String(stdout || '').split('\n').filter(l => l.includes(type));
      for (const l of lines) {
        const m = l.match(/(\d+\.\d+\.\d+\.\d+):(\d+)/);
        if (m && (!onlyIp || m[1] === onlyIp)) return resolve(m[1] + ':' + m[2]);
      }
      resolve(null);
    });
  });
  return (async () => {
    for (let i = 0; i < tries; i++) {
      if (stopRequested) return null;
      const found = await once();
      if (found) return found;
      if (i < tries - 1) await sleep(2000);   // discovery can lag a moment
    }
    return null;
  })();
}

// Backup when mdns finds nothing: look for open ports on the phone itself (Wireless debugging uses a random port)
async function scanOpenPorts(ip, from = 30000, to = 65535) {
  const open = [];
  let next = from;
  const probe = port => new Promise(resolve => {
    const sock = new net.Socket();
    let done = false;
    const end = ok => { if (done) return; done = true; sock.destroy(); if (ok) open.push(port); resolve(); };
    sock.setTimeout(500);
    sock.once('connect', () => end(true));
    sock.once('timeout', () => end(false));
    sock.once('error', () => end(false));
    sock.connect(port, ip);
  });
  const worker = async () => { while (next <= to && !stopRequested) await probe(next++); };
  await Promise.all(Array.from({ length: 500 }, worker));
  return open.sort((a, b) => a - b);
}

async function adbConnect(addr, timeout = 30000) {
  const r = await run('adb', ['connect', addr], { timeout });
  return /connected to/i.test(r.out) ? addr : null;
}
async function connectAny(ip, ports) {
  for (const port of ports) {
    if (stopRequested) break;
    const ok = await adbConnect(`${ip}:${port}`, 10000);
    if (ok) return ok;
  }
  return null;
}

async function pipeline(p) {
  logs = [];
  promptText = '';
  captionText = '';
  phoneState = null;
  feedbackItems = [];
  writeFeedback();
  stopRequested = false;
  setStatus('running');
  clients.forEach(c => c.write(`data: ${JSON.stringify({ reset: true })}\n\n`));
  try {
    let r;
    const needPhone = true;     // prompt + caption mode reads the product page in a PC browser: no phone, no adb, no Appium
    if (!needPhone) log('Prompt + caption mode: the phone is not needed, the product page is read in a browser on this PC.');
    if (needPhone) {
    // The code is given as an argument (more reliable on Windows than typing it into adb's prompt)
    const auto = p.connectMode === 'auto';
    let connect = null;
    let connected = false;

    if (auto) {
      if (!adbRestarted) {
        log('Restarting adb once so that phone discovery works...');
        await run('adb', ['kill-server'], { timeout: 10000 });
        adbRestarted = true;
      }
      // The phone that opened this page is the phone we connect to (found by its WiFi address)
      log(`Auto mode: looking for your phone (${p.clientIp}) on the WiFi...`);
      let ports = [];
      const found = await findService('_adb-tls-connect', 2, p.clientIp);
      if (found) {
        log('Phone found at ' + found);
        connect = await adbConnect(found);
      } else {
        log('The phone was not announced on the WiFi. What adb can see:');
        await run('adb', ['mdns', 'services'], { timeout: 10000 });
        log(`Scanning the phone (${p.clientIp}) for the Wireless debugging port. This can take up to 30 seconds...`);
        ports = await scanOpenPorts(p.clientIp);
        log('Open ports on the phone: ' + (ports.length ? ports.join(', ') : 'none'));
        connect = await connectAny(p.clientIp, ports);
      }
      connected = !!connect;

      // Not connected yet: pairing is needed (first time only) and needs the PIN from the phone
      if (!connected) {
        if (!p.pairPin) {
          log('\nCould not connect to your phone. Please check:');
          log('1) Wireless debugging is ON on the phone, and the phone is on the same WiFi as the PC (not mobile data, not a VPN).');
          log('2) First time (or after revoking authorizations): open "Pair device with pairing code" on the phone, keep that popup OPEN, type the 6-digit PIN in the PIN box here, then press Submit.');
          throw new Error('Phone not found or not paired');
        }
        const pairMdns = await findService('_adb-tls-pairing', 2, p.clientIp);
        if (!pairMdns && !ports.length) {
          log(`Scanning the phone (${p.clientIp}) for the pairing port...`);
          ports = await scanOpenPorts(p.clientIp);
          log('Open ports on the phone: ' + (ports.length ? ports.join(', ') : 'none'));
        }
        const candidates = pairMdns ? [pairMdns] : ports.map(x => `${p.clientIp}:${x}`);
        let pairedAddr = null;
        for (const addr of candidates) {
          r = await run('adb', ['pair', addr, p.pairPin], { timeout: 15000 });
          if (/Successfully paired/i.test(r.out)) { pairedAddr = addr; break; }
        }
        if (!pairedAddr) {
          log('\nHint: keep the "Pair device with pairing code" popup OPEN while you press Submit.');
          log('The code changes every time that popup is opened, and a code works only once.');
          throw new Error('ADB pairing failed');
        }
        const again = await findService('_adb-tls-connect', 3, p.clientIp);
        if (again) connect = await adbConnect(again);
        if (!connect) {
          if (!ports.length) ports = await scanOpenPorts(p.clientIp);
          connect = await connectAny(p.clientIp, ports.filter(x => `${p.clientIp}:${x}` !== pairedAddr));
        }
        connected = !!connect;
        if (!connected) throw new Error("Paired, but could not connect. Check Wireless debugging is ON, then press Submit again.");
      }
    } else {
      // Manual mode: use exactly what was typed on the page
      if (p.pairLink && p.pairPin) {
        r = await run('adb', ['pair', p.pairLink, p.pairPin], { timeout: 30000 });
        if (!/Successfully paired/i.test(r.out)) {
          log('\nHint: on the phone keep the "Pair device with pairing code" popup OPEN while you press Submit.');
          log('The code and the port change every time that popup is opened, and a code works only once.');
          log('Check that the IP and port match the popup exactly (the pairing port is NOT the one on the main Wireless debugging screen).');
          throw new Error('ADB pairing failed');
        }
      }
      connect = await adbConnect(p.connectLink);
      connected = !!connect;
    }
    if (!connected) throw new Error('ADB connect failed');

    r = await run('adb', ['devices']);
    const devs = r.out.split('\n').slice(1).filter(l => /\tdevice\s*$/.test(l.trim() + '\t' ) || /\sdevice$/.test(l.trim()));
    if (!devs.length) throw new Error('No device shown as connected in adb devices');
    log('Device verified.');

    await startAppium();

    updateEnv(ENV_USE_PORT ? connect : connect.split(':')[0]);
    }   // end of needPhone

    const noVoice = p.audio === 'none';
    const reelOnly = p.mode === 'reelpost';
    if (reelOnly) {
      try { fs.writeFileSync(CAPTION_FILE, String(p.caption || ''), 'utf8'); }
      catch (e) { throw new Error('Could not save the caption: ' + e.message); }
      log(`\nOnly post a Reel: the newest video in the phone's gallery will be posted with your caption (${String(p.caption || '').length} characters).`);
    } else {
      try { fs.unlinkSync(CAPTION_FILE); } catch {}   // an old caption must never be used by another run
    }
    log(`\nVideo type: ${p.videoType} | Style: ${p.reelFormat || 'auto'} | Audio: ${p.audio} | CTA: ${p.cta && !noVoice} | Text overlay: ${p.textOverlay ? (p.overlayOpts || []).join('+') : 'off'} | Mode: ${p.mode}`);
    r = await run('python', ['-u', 'main_link.py', ...(reelOnly ? [] : [p.productLink])], {
      cwd: WORKDIR,
      env: {
        PYTHONUNBUFFERED: '1',
        VIDEO_TYPE: p.videoType,
        CTA: String(!!p.cta && !noVoice),
        REEL_FORMAT: (p.reelFormat && p.reelFormat !== 'auto' && p.reelFormat !== 'custom') ? p.reelFormat : '',
        CUSTOM_STYLE: p.reelFormat === 'custom' ? String(p.customStyle || '').replace(/[\r\n\t]+/g, ' ').trim().slice(0, 300) : '',
        AUDIO_MODE: p.audio === 'voice' || p.audio === 'none' ? p.audio : '',
        TEXT_OVERLAY: String(p.textOverlay),
        TEXT_OVERLAY_OPTS: p.textOverlay ? (p.overlayOpts || []).join(',') : '',
        TEXT_OVERLAY_CUSTOM: p.textOverlay && (p.overlayOpts || []).includes('custom') ? String(p.overlayCustom || '').replace(/[\r\n\t]+/g, ' ').trim().slice(0, 40) : '',
        MODE: p.mode,
        CAPTION_FILE: reelOnly ? CAPTION_FILE : '',      // "Only post a Reel": the caption typed on the page
        FEEDBACK_FILE,                                  // live notes from the page
        REUSE_RUN: p.redoDir || '',                     // "Make again": re-use the screenshots + script of that folder
        USER_FEEDBACK: p.feedback || '',                // "Make again": the change you asked for
        RETURN_TO_BROWSER: p.connectMode === 'auto' ? '1' : '0',   // started from the phone: bring its browser back whenever the phone is not needed
        SCRAPE: 'phone',   // no phone in this mode: the page must be read on the PC
      },
    });
    if (r.code !== 0) throw new Error('main_link.py exited with code ' + r.code);
    log('\n✅ Finished.');
    setStatus('done');
  } catch (e) {
    if (stopRequested || e.message === 'STOP') {
      log('\n⏹ Stopped by user. (Appium is left running for the next start.)');
      setStatus('stopped');
    } else {
      log('\n❌ ' + e.message);
      setStatus('error');
    }
  }
}

const ADDR = /^[\w.\-]+:\d{2,5}$/;
const PIN = /^\d{4,8}$/;
const VIDEO = ['hands', 'person', 'pet', 'animated'];
// Every mode includes the ones before it: prompt = YT Create prompt + caption | video = + video | post = + Instagram post | autodm = + Meesho auto-DM
// autodmonly stands alone: no video, no post; it only wishlists the product and links the Meesho auto-DM to the most recent Instagram post (for testing)
// reelpost stands alone: it only posts the newest video of the phone's gallery as a Reel, with the caption typed on the page
const MODES = ['prompt', 'video', 'post', 'autodm', 'autodmonly', 'reelpost'];
const LEGACY_MODES = { prompt_only: 'prompt', yt_only: 'video', yt_instagram: 'autodm' };   // old page versions
const CONNECT_MODES = ['auto', 'manual'];
const AUDIO = ['auto', 'voice', 'none'];            // auto = random, voice = someone talks/narrates/sings, none = music or sounds only
const OVERLAY_IDS = ['name', 'cute', 'hook', 'feature', 'custom', 'comment', 'dm', 'follow', 'bio'];   // texts that can be shown on the video
const STYLE_ID = /^[a-z0-9_]{0,40}$/;              // a format id from reel_styles.py ('' or 'auto' = random)

// The list of video styles comes from reel_styles.py itself, so the dropdown never goes out of date.
let stylesCache = null;
function getStyles(cb) {
  if (stylesCache) return cb(stylesCache);
  execFile('python', ['reel_styles.py', '--json'], { cwd: WORKDIR, timeout: 10000 }, (err, stdout) => {
    try { const j = JSON.parse(String(stdout).trim()); if (Array.isArray(j) && j.length) stylesCache = j; return cb(j); }
    catch { return cb([]); }
  });
}

function validate(b) {
  if (LEGACY_MODES[b.mode]) b.mode = LEGACY_MODES[b.mode];
  if (!MODES.includes(b.mode)) return 'Invalid mode';
  if (b.mode === 'reelpost' || b.mode === 'autodmonly') {     // no video is made: ignore the video-only choices left over on the page
    b.textOverlay = false;
    if (b.reelFormat === 'custom') b.reelFormat = 'auto';
    if (!VIDEO.includes(b.videoType)) b.videoType = 'hands';
  }
  if (b.mode !== 'prompt') {      // phone details are not needed for prompt + caption mode
  if (!CONNECT_MODES.includes(b.connectMode)) return 'Invalid connect mode';
  if (b.pairPin && !PIN.test(b.pairPin)) return 'Invalid pair PIN';
  if (b.connectMode === 'manual') {
    if (!ADDR.test(b.connectLink || '')) return 'Please enter the ADB connect link (ip:port)';
    if (b.pairLink && !ADDR.test(b.pairLink)) return 'Invalid ADB pair link (expected ip:port)';
    if ((b.pairLink || b.pairPin) && !(b.pairLink && b.pairPin)) return 'For pairing, fill both the pair link and the pair PIN';
  }
  }
  if (b.mode === 'reelpost') {
    const cap = String(b.caption || '').replace(/\r\n?/g, '\n');
    if (!cap.trim()) return 'Type the caption for the Reel.';
    if (cap.length > 2200) return 'The caption is too long (Instagram allows 2,200 characters).';
  } else {
    try { const u = new URL(b.productLink); if (!/^https?:$/.test(u.protocol)) throw 0; } catch { return 'Invalid product link'; }
    if (/["'`&|<>^\s]/.test(b.productLink)) return 'Product link has unsafe characters';
  }
  if (!VIDEO.includes(b.videoType)) return 'Invalid video type';
  if (b.audio !== undefined && !AUDIO.includes(b.audio)) return 'Invalid audio choice';
  if (b.reelFormat !== undefined && !STYLE_ID.test(b.reelFormat)) return 'Invalid video style';
  if (b.reelFormat === 'custom' && !String(b.customStyle || '').trim()) return 'Please describe your own video style (or pick another style)';
  if (b.customStyle !== undefined && String(b.customStyle).length > 300) return 'Custom style is too long (max 300 characters)';
  if (b.textOverlay) {
    const o = Array.isArray(b.overlayOpts) ? b.overlayOpts : [];
    if (!o.length) return 'Pick at least one text for the video (or untick "Text on video")';
    if (o.length > 3) return 'Pick at most 3 texts for the video';
    if (!o.every(x => OVERLAY_IDS.includes(x)) || new Set(o).size !== o.length) return 'Invalid text option';
    if (o.includes('custom') && !String(b.overlayCustom || '').trim()) return 'Type your own text (or untick "My own text")';
    if (o.includes('dm') && !o.includes('comment')) return '"Link in your DM" only works together with "Comment LINK". Tick both, or untick "Link in your DM"';
  }
  if (b.overlayCustom !== undefined && String(b.overlayCustom).length > 40) return 'Your own text is too long (max 40 characters)';
  if (!MODES.includes(b.mode)) return 'Invalid mode';
  return null;
}

http.createServer((req, res) => {
  if (req.url.startsWith('/history') && handleHistory(req, res)) return;
  if (req.method === 'GET' && (req.url === '/' || req.url === '/index.html')) {
    const file = path.join(__dirname, 'index.html');
    return fs.readFile(file, (err, data) => {
      if (err) {   // do not crash the server when index.html is missing: say so on the page
        res.writeHead(500, { 'Content-Type': 'text/plain; charset=utf-8' });
        return res.end('index.html is missing. Put index.html in this folder: ' + __dirname);
      }
      res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
      res.end(data);
    });
  }
  if (req.method === 'GET' && req.url === '/styles') {
    return getStyles(list => { res.writeHead(200, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(list)); });
  }
  if (req.method === 'GET' && req.url === '/events') {
    res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache', Connection: 'keep-alive' });
    res.write(`data: ${JSON.stringify({ status, lines: logs, appiumLines: appiumLogs, prompt: promptText, caption: captionText, phone: phonePayload(), params: lastParams })}\n\n`);
    clients.push(res);
    req.on('close', () => { clients = clients.filter(c => c !== res); });
    return;
  }
  if (req.method === 'POST' && req.url === '/run') {
    let body = '';
    req.on('data', d => { body += d; if (body.length > 1e5) req.destroy(); });
    req.on('end', async () => {
      let b; try { b = JSON.parse(body); } catch { res.writeHead(400); return res.end('Bad JSON'); }
      const err = validate(b);
      if (err) { res.writeHead(400); return res.end(err); }
      // "Make again with this change": needs the saved screenshots + script of the last run
      let redoDir = '';
      if (b.redo) {
        try { const st = JSON.parse(fs.readFileSync(path.join(lastRunDir, 'run_state.json'), 'utf8')); if (st.capture && st.script) redoDir = lastRunDir; } catch (e) {}
        if (!redoDir) { res.writeHead(400); return res.end('There is no saved run to repeat yet. Run the bot once first (the script and screenshots are saved when a run gets that far).'); }
      }
      const feedback = String(b.feedback || '').replace(/[\r\n\t]+/g, ' ').trim().slice(0, 500);
      // Auto mode connects to the device that opened this page, so we need to know its address
      const clientIp = String(req.socket.remoteAddress || '').replace(/^::ffff:/, '');
      if (b.connectMode === 'auto' && b.mode !== 'prompt') {
        const mine = new Set(['127.0.0.1', '::1']);
        Object.values(os.networkInterfaces()).flat().forEach(i => mine.add(i.address));
        if (mine.has(clientIp)) {
          res.writeHead(400);
          return res.end('Auto connect only works when this page is opened on the phone itself. On the PC, choose "Manual".');
        }
      }
      if (status === 'running') {
        if (!b.restart) { res.writeHead(409); return res.end('A job is already running'); }
        await stopJob();          // Start again while running = stop first, then start fresh
      }
      lastParams = { pairLink: b.pairLink, pairPin: b.pairPin, connectLink: b.connectLink, productLink: b.productLink,
                     videoType: b.videoType, reelFormat: b.reelFormat || 'auto', customStyle: String(b.customStyle || ''), audio: b.audio || 'auto', cta: !!b.cta, textOverlay: !!b.textOverlay, overlayOpts: Array.isArray(b.overlayOpts) ? b.overlayOpts.filter(x => OVERLAY_IDS.includes(x)).slice(0, 3) : [], overlayCustom: String(b.overlayCustom || '').slice(0, 40), mode: b.mode, connectMode: b.connectMode,
                     caption: String(b.caption || '').replace(/\r\n?/g, '\n').slice(0, 2200) };
      res.writeHead(200); res.end('started');
      startHistory(lastParams, { redo: !!b.redo, feedback });      // saved in the History tab
      jobPromise = pipeline({ ...lastParams, clientIp, redoDir, feedback });
    });
    return;
  }
  if (req.method === 'POST' && req.url === '/feedback') {     // a note for the RUNNING bot
    let body = '';
    req.on('data', d => { body += d; if (body.length > 1e4) req.destroy(); });
    req.on('end', () => {
      let b; try { b = JSON.parse(body); } catch { res.writeHead(400); return res.end('Bad JSON'); }
      const text = String(b.text || '').replace(/[\r\n\t]+/g, ' ').trim().slice(0, 500);
      if (text.length < 3) { res.writeHead(400); return res.end('Write a few words first.'); }
      if (status !== 'running') { res.writeHead(409); return res.end('Nothing is running right now. Use "Make again with this change".'); }
      const kind = b.kind === 'phone' ? 'phone' : 'video';
      feedbackItems.push({ id: feedbackItems.length + 1, kind, text });
      writeFeedback();
      log(`\n\u270D Your note (${kind === 'phone' ? 'help on the phone' : 'change the video'}): ${text}`);
      res.writeHead(200); res.end('sent');
    });
    return;
  }
  if (req.method === 'POST' && req.url === '/clear') {
    logs = [];   // clears the log for every device (status and the YT prompt box stay)
    clients.forEach(c => c.write(`data: ${JSON.stringify({ lines: [] })}\n\n`));
    res.writeHead(200); return res.end('cleared');
  }
  if (req.method === 'POST' && req.url === '/clear-appium') {
    appiumLogs = [];
    clients.forEach(c => c.write(`data: ${JSON.stringify({ appiumLines: [] })}\n\n`));
    res.writeHead(200); return res.end('cleared');
  }
  if (req.method === 'POST' && req.url === '/stop') {
    stopJob().then(() => { res.writeHead(200); res.end('stopped'); });
    return;
  }
  res.writeHead(404); res.end('Not found');
}).listen(PORT, '0.0.0.0', () => {
  console.log('Affiliate Bot UI running.');
  console.log(`  This PC:  http://localhost:${PORT}`);
  Object.values(os.networkInterfaces()).flat()
    .filter(i => i.family === 'IPv4' && !i.internal)
    .forEach(i => console.log(`  Phone (same WiFi): http://${i.address}:${PORT}`));
});
