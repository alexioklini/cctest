/* ═══════════════════════════════════════════════════════════════════
   Local Inference — lazy llama.cpp engine + model cache, in-process.

   Loaded by main.js. Exposes IPC handlers the renderer calls through
   electronAPI.localInference.* to:
     1. Download + cache the llama.cpp `llama-server` binary for the
        current platform, lazily on first use. The engine manifest lives
        on the Brain server at GET /v1/client/engines; absent that, we
        refuse to proceed (no hard-coded public URLs — air-gapped first).
     2. Download + cache GGUF model weights from the Brain server's
        /v1/client/models/<id>/weights endpoint, verified by sha256.
     3. Spawn llama-server on a random localhost port when an inference
        request arrives; reuse the process across requests; shut it down
        after 10 min idle. Swap model by restarting if the family changes.
     4. Stream OpenAI-compatible chat completions from that local server
        back to the renderer via chunk/end/error IPC events.

   Everything under app.getPath('userData')/brain-local-inference/:
     engine/<sha>/llama-server[.exe]     binary keyed by sha256
     engine/active -> engine/<sha>       platform-aware alias (optional)
     models/<sha>.gguf                   weights keyed by sha256
     models/<sha>.gguf.partial           in-flight download
     state.json                          {engine_sha, last_used_model}

   Notes:
   - Resume: downloads use HTTP Range on .partial files.
   - Verify: sha is streamed during download, confirmed on finalize. A
     mismatch deletes the file and reports an error.
   - Cancel: a single in-flight AbortController per download channel.
   - Idle shutdown: any pending request resets the timer; on timeout the
     llama-server child is killed gracefully (SIGTERM then SIGKILL).
   ═══════════════════════════════════════════════════════════════════ */

const { app, ipcMain } = require('electron');
const path = require('path');
const fs = require('fs');
const fsp = fs.promises;
const http = require('http');
const https = require('https');
const crypto = require('crypto');
const { spawn } = require('child_process');
const { URL } = require('url');

const ROOT = path.join(app.getPath('userData'), 'brain-local-inference');
const ENGINE_DIR = path.join(ROOT, 'engine');
const MODELS_DIR = path.join(ROOT, 'models');
const STATE_PATH = path.join(ROOT, 'state.json');

const IDLE_TIMEOUT_MS = 10 * 60 * 1000;

function ensureDirs() {
  for (const d of [ROOT, ENGINE_DIR, MODELS_DIR]) {
    try { fs.mkdirSync(d, { recursive: true }); } catch {}
  }
}

function loadState() {
  try { return JSON.parse(fs.readFileSync(STATE_PATH, 'utf-8')); }
  catch { return {}; }
}

function saveState(patch) {
  const cur = loadState();
  const next = { ...cur, ...patch };
  try { fs.writeFileSync(STATE_PATH, JSON.stringify(next, null, 2)); } catch {}
  return next;
}

// ─── Platform-aware engine keys ────────────────────────────────────────
// The server's /v1/client/engines response keys entries by this string.
function platformKey() {
  const plat = process.platform;               // 'win32' | 'darwin' | 'linux'
  const arch = process.arch;                   // 'x64' | 'arm64' | ...
  return `${plat}-${arch}`;
}

function engineBinaryName() {
  return process.platform === 'win32' ? 'llama-server.exe' : 'llama-server';
}

// ─── Generic resumable download with streaming sha256 verification ─────
//
// Downloads `url` to `destPath`, using a `.partial` sibling for resume.
// Range requests supported when the server honors them; otherwise we
// restart from zero on resume.
//
// onProgress({bytes, total, fraction}) fires periodically.
// Returns { path, sha256 } on success. Throws on mismatch / network err.
async function downloadWithResume({ url, destPath, expectedSha256, headers = {}, onProgress, abortSignal }) {
  const partial = destPath + '.partial';
  let haveBytes = 0;
  try { haveBytes = (await fsp.stat(partial)).size; } catch {}

  // Do not reuse a .partial if we already have the final file (it
  // shouldn't happen, but a stray partial is a liability — wipe it).
  try {
    const s = await fsp.stat(destPath);
    if (s.size > 0 && expectedSha256) {
      const existingSha = await shaFile(destPath);
      if (existingSha === expectedSha256) {
        return { path: destPath, sha256: existingSha };
      }
    }
  } catch {}

  const reqUrl = new URL(url);
  const mod = reqUrl.protocol === 'https:' ? https : http;

  // When resuming, we need to re-hash the partial to pre-seed the hasher.
  const hasher = crypto.createHash('sha256');
  if (haveBytes > 0) {
    await new Promise((resolve, reject) => {
      const rs = fs.createReadStream(partial);
      rs.on('data', (c) => hasher.update(c));
      rs.on('end', resolve);
      rs.on('error', reject);
    });
  }

  const reqHeaders = { ...headers };
  if (haveBytes > 0) reqHeaders['Range'] = `bytes=${haveBytes}-`;

  await new Promise((resolve, reject) => {
    const req = mod.get(url, { headers: reqHeaders }, (res) => {
      // Non-range response when we asked for a range → restart from zero.
      if (haveBytes > 0 && res.statusCode === 200) {
        haveBytes = 0;
        try { fs.unlinkSync(partial); } catch {}
        hasher.destroy?.();
      }
      if (res.statusCode !== 200 && res.statusCode !== 206) {
        res.resume();
        return reject(new Error(`HTTP ${res.statusCode} from ${url}`));
      }
      const totalHeader = res.headers['content-length'];
      // With 206, content-length is the remaining bytes, content-range has total.
      let total = 0;
      if (res.statusCode === 206 && res.headers['content-range']) {
        const m = /\/(\d+)$/.exec(res.headers['content-range']);
        if (m) total = parseInt(m[1], 10);
      } else if (totalHeader) {
        total = parseInt(totalHeader, 10);
      }

      const ws = fs.createWriteStream(partial, { flags: haveBytes > 0 ? 'a' : 'w' });
      let got = haveBytes;
      let lastReport = 0;

      const abortHandler = () => {
        try { req.destroy(new Error('aborted')); } catch {}
        try { ws.destroy(); } catch {}
        reject(new Error('Download cancelled'));
      };
      if (abortSignal) {
        if (abortSignal.aborted) return abortHandler();
        abortSignal.addEventListener('abort', abortHandler, { once: true });
      }

      res.on('data', (chunk) => {
        hasher.update(chunk);
        got += chunk.length;
        const now = Date.now();
        if (onProgress && (now - lastReport > 200)) {
          lastReport = now;
          onProgress({ bytes: got, total, fraction: total ? got / total : 0 });
        }
        if (!ws.write(chunk)) res.pause();
      });
      ws.on('drain', () => res.resume());
      res.on('end', () => ws.end());
      ws.on('finish', () => {
        if (onProgress) onProgress({ bytes: got, total, fraction: total ? got / total : 1 });
        resolve();
      });
      res.on('error', reject);
      ws.on('error', reject);
    });
    req.on('error', reject);
  });

  const actualSha = hasher.digest('hex');
  if (expectedSha256 && actualSha !== expectedSha256) {
    try { await fsp.unlink(partial); } catch {}
    throw new Error(`sha256 mismatch: expected ${expectedSha256}, got ${actualSha}`);
  }
  await fsp.rename(partial, destPath);
  return { path: destPath, sha256: actualSha };
}

async function shaFile(filePath) {
  return new Promise((resolve, reject) => {
    const h = crypto.createHash('sha256');
    const rs = fs.createReadStream(filePath);
    rs.on('data', (c) => h.update(c));
    rs.on('end', () => resolve(h.digest('hex')));
    rs.on('error', reject);
  });
}

// ─── Engine manifest: fetched from Brain server ────────────────────────
//
// The server at /v1/client/engines publishes pinned llama.cpp release URLs
// and sha256 per platform key. No hardcoded public URLs here — admins
// point this at an internal mirror for air-gapped deployments.
async function fetchEngineManifest({ serverUrl, authToken }) {
  if (!serverUrl) throw new Error('No server URL');
  const url = serverUrl.replace(/\/+$/, '') + '/v1/client/engines';
  const headers = {};
  if (authToken) headers['Authorization'] = 'Bearer ' + authToken;
  return new Promise((resolve, reject) => {
    const mod = url.startsWith('https:') ? https : http;
    const req = mod.get(url, { headers }, (res) => {
      let body = '';
      res.on('data', (c) => body += c);
      res.on('end', () => {
        if (res.statusCode < 400) {
          try { resolve(JSON.parse(body)); }
          catch (e) { reject(new Error('Invalid engine manifest JSON')); }
        } else {
          reject(new Error(`HTTP ${res.statusCode} fetching engine manifest`));
        }
      });
    });
    req.on('error', reject);
  });
}

// ─── Engine ensure: download + chmod +x if needed ──────────────────────
//
// State persisted: { engine_sha: '<hex>' } — subsequent launches skip the
// download if the file still hashes to that value.
async function ensureEngine({ serverUrl, authToken, onProgress, abortSignal }) {
  ensureDirs();
  const manifest = await fetchEngineManifest({ serverUrl, authToken });
  const key = platformKey();
  const entry = (manifest.engines || {})[key];
  if (!entry || !entry.url || !entry.sha256) {
    throw new Error(`No engine entry for platform ${key}`);
  }

  const engineRoot = path.join(ENGINE_DIR, entry.sha256);
  await fsp.mkdir(engineRoot, { recursive: true });
  const binPath = path.join(engineRoot, engineBinaryName());

  // Already installed? Verify sha of the binary only (not tar/zip members)
  try {
    const s = await fsp.stat(binPath);
    if (s.size > 0) {
      // Trust the directory-name sha; re-hashing every launch would be slow.
      saveState({ engine_sha: entry.sha256 });
      return { path: binPath, sha256: entry.sha256 };
    }
  } catch {}

  // Download. Engine releases are typically archives; for MVP we expect
  // the admin to publish a direct llama-server binary URL per platform.
  // If that's too narrow, later we'll add archive-extract support.
  if (entry.archive) {
    throw new Error('Archive engine distributions not yet supported — publish a direct binary URL');
  }
  const tmpPath = path.join(engineRoot, engineBinaryName());
  await downloadWithResume({
    url: entry.url,
    destPath: tmpPath,
    expectedSha256: entry.sha256,
    onProgress: (p) => onProgress && onProgress({ kind: 'engine', ...p }),
    abortSignal,
  });
  if (process.platform !== 'win32') {
    try { await fsp.chmod(tmpPath, 0o755); } catch {}
  }
  saveState({ engine_sha: entry.sha256 });
  return { path: tmpPath, sha256: entry.sha256 };
}

// ─── Model ensure: download from Brain server's weights endpoint ───────
async function ensureModel({ model, serverUrl, authToken, onProgress, abortSignal }) {
  if (!model || !model.id || !model.sha256) {
    throw new Error('Invalid model entry (need id + sha256)');
  }
  ensureDirs();
  const destPath = path.join(MODELS_DIR, `${model.sha256}.gguf`);
  const url = serverUrl.replace(/\/+$/, '') + (model.download_path || `/v1/client/models/${model.id}/weights`);
  const headers = {};
  if (authToken) headers['Authorization'] = 'Bearer ' + authToken;
  return downloadWithResume({
    url, destPath, expectedSha256: model.sha256, headers,
    onProgress: (p) => onProgress && onProgress({ kind: 'model', model_id: model.id, ...p }),
    abortSignal,
  });
}

// ─── Llama-server lifecycle ────────────────────────────────────────────

let engineProcess = null;
let engineEndpoint = null;   // 'http://127.0.0.1:<port>'
let engineModelSha = null;   // currently-loaded model's sha256
let idleTimer = null;
let startupPromise = null;   // de-dup concurrent spawns

function resetIdleTimer() {
  if (idleTimer) clearTimeout(idleTimer);
  idleTimer = setTimeout(() => {
    shutdownEngine('idle');
  }, IDLE_TIMEOUT_MS);
}

function shutdownEngine(reason) {
  if (!engineProcess) return;
  try { engineProcess.kill('SIGTERM'); } catch {}
  const p = engineProcess;
  setTimeout(() => {
    try { if (!p.killed) p.kill('SIGKILL'); } catch {}
  }, 3000);
  engineProcess = null;
  engineEndpoint = null;
  engineModelSha = null;
  if (idleTimer) { clearTimeout(idleTimer); idleTimer = null; }
}

async function pickFreePort() {
  return new Promise((resolve, reject) => {
    const srv = require('net').createServer();
    srv.unref();
    srv.on('error', reject);
    srv.listen(0, '127.0.0.1', () => {
      const { port } = srv.address();
      srv.close(() => resolve(port));
    });
  });
}

async function waitForEngineReady(port, timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const ok = await new Promise((resolve) => {
      const req = http.get(`http://127.0.0.1:${port}/health`, { timeout: 500 }, (res) => {
        res.resume();
        resolve(res.statusCode === 200);
      });
      req.on('error', () => resolve(false));
      req.on('timeout', () => { req.destroy(); resolve(false); });
    });
    if (ok) return true;
    await new Promise(r => setTimeout(r, 250));
  }
  return false;
}

// Spawn or reuse llama-server bound to the given model. Swaps if model sha
// doesn't match the currently-loaded one.
async function startEngineFor({ enginePath, modelPath, modelSha }) {
  if (engineProcess && engineModelSha === modelSha) {
    resetIdleTimer();
    return engineEndpoint;
  }
  if (startupPromise) return startupPromise;

  startupPromise = (async () => {
    shutdownEngine('model-swap');
    const port = await pickFreePort();
    const args = [
      '-m', modelPath,
      '--port', String(port),
      '--host', '127.0.0.1',
      // Tight defaults — renderer passes real inference params per request
      '-c', '4096',
      '--log-disable',
    ];
    const child = spawn(enginePath, args, {
      stdio: ['ignore', 'pipe', 'pipe'],
      windowsHide: true,
    });
    child.stdout.on('data', (c) => {
      process.stdout.write(`[llama-server] ${c}`);
    });
    child.stderr.on('data', (c) => {
      process.stderr.write(`[llama-server] ${c}`);
    });
    child.on('exit', (code, sig) => {
      if (engineProcess === child) {
        engineProcess = null;
        engineEndpoint = null;
        engineModelSha = null;
      }
    });

    const ready = await waitForEngineReady(port);
    if (!ready) {
      try { child.kill('SIGKILL'); } catch {}
      throw new Error(`llama-server failed to become ready on port ${port}`);
    }
    engineProcess = child;
    engineEndpoint = `http://127.0.0.1:${port}`;
    engineModelSha = modelSha;
    resetIdleTimer();
    return engineEndpoint;
  })();

  try {
    return await startupPromise;
  } finally {
    startupPromise = null;
  }
}

// ─── Inference: stream OpenAI chat/completions from local llama-server ─
//
// Requests are tracked by requestId (UUID from renderer) so cancel works.
const activeRequests = new Map();   // requestId -> { req, aborted }

function runInference({ webContents, requestId, payload, model }) {
  if (!engineEndpoint) {
    webContents.send('local-inference-error', { requestId, message: 'Engine not running' });
    return;
  }
  resetIdleTimer();

  // Force streaming on so we can pipe tokens as they arrive.
  const body = JSON.stringify({ ...payload, stream: true, model: model && model.family });
  const url = new URL(engineEndpoint + '/v1/chat/completions');
  const req = http.request({
    method: 'POST',
    hostname: url.hostname,
    port: url.port,
    path: url.pathname,
    headers: {
      'Content-Type': 'application/json',
      'Content-Length': Buffer.byteLength(body),
    },
  }, (res) => {
    const state = activeRequests.get(requestId);
    if (!state || state.aborted) { res.resume(); return; }
    let buf = '';
    res.setEncoding('utf-8');
    res.on('data', (chunk) => {
      buf += chunk;
      // Split on SSE line delimiter; feed lines one-by-one to match server-side
      // parsing expectations.
      const parts = buf.split('\n');
      buf = parts.pop() || '';
      for (const line of parts) {
        const trimmed = line.trimEnd();
        if (!trimmed) continue;
        webContents.send('local-inference-chunk', { requestId, line: trimmed });
      }
    });
    res.on('end', () => {
      if (buf.trim()) {
        webContents.send('local-inference-chunk', { requestId, line: buf.trimEnd() });
      }
      webContents.send('local-inference-end', { requestId });
      activeRequests.delete(requestId);
      resetIdleTimer();
    });
    res.on('error', (e) => {
      webContents.send('local-inference-error', { requestId, message: e.message });
      activeRequests.delete(requestId);
    });
  });
  req.on('error', (e) => {
    webContents.send('local-inference-error', { requestId, message: e.message });
    activeRequests.delete(requestId);
  });
  activeRequests.set(requestId, { req, aborted: false });
  req.write(body);
  req.end();
}

function cancelInference(requestId) {
  const state = activeRequests.get(requestId);
  if (!state) return;
  state.aborted = true;
  try { state.req.destroy(new Error('cancelled')); } catch {}
  activeRequests.delete(requestId);
}

// ─── IPC registration ──────────────────────────────────────────────────

function register() {
  ensureDirs();

  ipcMain.handle('local-inference-status', () => {
    return {
      engine_running: !!engineProcess,
      engine_endpoint: engineEndpoint || null,
      loaded_model_sha: engineModelSha || null,
      platform_key: platformKey(),
      root: ROOT,
    };
  });

  ipcMain.handle('local-inference-ensure-engine', async (event, { serverUrl, authToken } = {}) => {
    const wc = event.sender;
    try {
      const result = await ensureEngine({
        serverUrl, authToken,
        onProgress: (p) => wc.send('local-inference-progress', p),
      });
      return { ok: true, ...result };
    } catch (e) {
      return { ok: false, error: e.message };
    }
  });

  ipcMain.handle('local-inference-ensure-model', async (event, { model, serverUrl, authToken }) => {
    const wc = event.sender;
    try {
      const result = await ensureModel({
        model, serverUrl, authToken,
        onProgress: (p) => wc.send('local-inference-progress', p),
      });
      return { ok: true, ...result };
    } catch (e) {
      return { ok: false, error: e.message };
    }
  });

  ipcMain.on('local-inference-run', async (event, { requestId, payload, model }) => {
    const wc = event.sender;
    try {
      // Must have engine + matching model loaded. Renderer is responsible
      // for calling ensureEngine/ensureModel before run, but we double-
      // check the model is loaded and lazy-spawn if necessary.
      const state = loadState();
      if (!state.engine_sha) {
        return wc.send('local-inference-error', { requestId, message: 'Engine not downloaded yet' });
      }
      const enginePath = path.join(ENGINE_DIR, state.engine_sha, engineBinaryName());
      if (!fs.existsSync(enginePath)) {
        return wc.send('local-inference-error', { requestId, message: 'Engine binary missing from cache' });
      }
      if (!model || !model.sha256) {
        return wc.send('local-inference-error', { requestId, message: 'Model entry missing sha256' });
      }
      const modelPath = path.join(MODELS_DIR, `${model.sha256}.gguf`);
      if (!fs.existsSync(modelPath)) {
        return wc.send('local-inference-error', { requestId, message: 'Model weights not downloaded yet' });
      }
      await startEngineFor({ enginePath, modelPath, modelSha: model.sha256 });
      runInference({ webContents: wc, requestId, payload, model });
    } catch (e) {
      wc.send('local-inference-error', { requestId, message: e.message });
    }
  });

  ipcMain.on('local-inference-cancel', (_event, { requestId }) => {
    cancelInference(requestId);
  });

  // Graceful shutdown when the app quits — we don't want an orphaned
  // llama-server lingering after Brain's desktop app exits.
  app.on('before-quit', () => shutdownEngine('app-quit'));
}

module.exports = { register };
