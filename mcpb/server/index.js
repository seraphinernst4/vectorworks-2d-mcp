#!/usr/bin/env node
// Keystone for Vectorworks – Claude Desktop extension launcher.
//
// Claude Desktop runs this with its built-in Node and talks to it over
// stdin/stdout (MCP, JSON-RPC, one message per line). Like Keystone for
// Rhino, the launcher answers the connection handshake at once: Claude probes
// every new connection with server/discover and drops it if the answer takes
// more than a few seconds, while the Python server needs longer to start
// (much longer the first time, when uv loads Python and the packages).
//
// So: server/discover, ping, initialize and (until the server runs)
// tools/list are answered here, from static.json, which the build writes from
// the real server. Everything else goes to the Python server
// (server/main.py, run with uv; uv is found or downloaded once into
// ~/.vwmcp/uv). If the server can't start, tool calls return why.
// Log: ~/.vwmcp/extension.log
"use strict";

const { spawn, execFileSync } = require("child_process");
const fs = require("fs");
const https = require("https");
const os = require("os");
const path = require("path");
const readline = require("readline");

const HOME = os.homedir();
const ROOT = process.env.VWMCP_HOME || path.join(HOME, ".vwmcp");
const LOG = path.join(ROOT, "extension.log");
const BUNDLE = path.resolve(__dirname, "..");
const NAME = "Keystone for Vectorworks";

function readJson(p) {
  try { return JSON.parse(fs.readFileSync(p, "utf8")); } catch (e) { return null; }
}
const VERSION = (readJson(path.join(BUNDLE, "manifest.json")) || {}).version || "";
const STATIC = readJson(path.join(__dirname, "static.json")); // {protocolVersions, initialize, tools}

function log(msg) {
  const line = `${new Date().toISOString()} ${msg}`;
  try {
    fs.mkdirSync(ROOT, { recursive: true });
    fs.appendFileSync(LOG, line + "\n");
  } catch (e) { /* logging must never stop the server */ }
  try { process.stderr.write(line + "\n"); } catch (e) { /* */ }
}

try { if (fs.statSync(LOG).size > 2e6) fs.renameSync(LOG, LOG + ".old"); } catch (e) { /* */ }

// ---- talking to Claude ----------------------------------------------------

function send(msg) {
  try { process.stdout.write(JSON.stringify(msg) + "\n"); } catch (e) { log(`write failed: ${e.message}`); }
}
const reply = (id, result) => send({ jsonrpc: "2.0", id, result });
const replyError = (id, code, message) => send({ jsonrpc: "2.0", id, error: { code, message } });
const keyOf = (id) => `${typeof id}:${id}`;

function initializeResult(params) {
  const versions = (STATIC && STATIC.protocolVersions) || ["2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"];
  const asked = params && params.protocolVersion;
  const base = (STATIC && STATIC.initialize) || {
    capabilities: { tools: {} },
    serverInfo: { name: NAME, version: VERSION },
  };
  return { ...base, protocolVersion: versions.includes(asked) ? asked : versions[versions.length - 1] };
}

// ---- state ----------------------------------------------------------------

let child = null;
let childReady = false; // the server answered its initialize
let initKey = null;
let failure = null; // why the server could not start (fallback mode)
let stdinClosed = false;
let stderrTail = "";
const toChild = []; // lines waiting for the server process
const outstanding = new Map(); // requests the server still has to answer
const swallow = new Set(); // server answers already given here

function report() {
  return `${NAME} ${VERSION} konnte den Server nicht starten / could not start its server.\n\n` +
    `${failure}\n\n${stderrTail.trim() ? "Ausgabe / output:\n" + stderrTail.trim() + "\n\n" : ""}` +
    `Log: ${LOG}\nTipp: Claude beenden und neu öffnen. Hilft das nicht, den Installer erneut ausführen ` +
    `(README: https://github.com/seraphinernst4/vectorworks-2d-mcp).`;
}

const PING_TOOL = {
  name: "vw_ping",
  description: `${NAME} could not start its server – call this to see why and tell the user.`,
  inputSchema: { type: "object", properties: {} },
};

function answerHere(msg) {
  // requests while the server can't run
  const { id, method } = msg;
  switch (method) {
    case "initialize": {
      const r = initializeResult(msg.params);
      r.instructions = `${NAME} could not start its server. Call vw_ping to see why and tell the user.`;
      return reply(id, r);
    }
    case "tools/list": return reply(id, { tools: [PING_TOOL] });
    case "tools/call": return reply(id, { content: [{ type: "text", text: report() }], isError: true });
    case "resources/list": return reply(id, { resources: [] });
    case "resources/templates/list": return reply(id, { resourceTemplates: [] });
    case "prompts/list": return reply(id, { prompts: [] });
    default: return replyError(id, -32603, report());
  }
}

function stopBeat(entry) {
  if (entry && entry.beat) clearInterval(entry.beat);
}

function answerOutstanding() {
  for (const entry of outstanding.values()) {
    stopBeat(entry);
    answerHere(entry.msg);
  }
  outstanding.clear();
}

function toServer(line) {
  if (child && child.stdin.writable) child.stdin.write(line + "\n");
  else toChild.push(line);
}

// ---- messages from Claude -------------------------------------------------

function fromClaude(line) {
  const t = line.trim();
  if (!t) return;
  let msg;
  try { msg = JSON.parse(t); } catch (e) { msg = null; }
  if (!msg || Array.isArray(msg) || typeof msg !== "object") {
    if (failure === null) toServer(t); // let the server answer what we don't understand
    return;
  }
  const isRequest = msg.method !== undefined && msg.id !== undefined && msg.id !== null;
  if (isRequest) {
    // answered here, at once
    if (msg.method === "server/discover") return replyError(msg.id, -32601, "Method not found: server/discover");
    if (msg.method === "ping") return reply(msg.id, {});
    if (failure !== null) return answerHere(msg);
    if (msg.method === "initialize") {
      reply(msg.id, initializeResult(msg.params));
      initKey = keyOf(msg.id);
      swallow.add(initKey);
      return toServer(t); // the server still needs it for its session
    }
    if (msg.method === "tools/list" && !childReady && STATIC && STATIC.tools) {
      return reply(msg.id, { tools: STATIC.tools });
    }
    const entry = { msg };
    const token = msg.method === "tools/call" && msg.params && msg.params._meta && msg.params._meta.progressToken;
    if (token !== undefined && token !== null && token !== false) {
      let n = 0;
      entry.beat = setInterval(() => {
        n++;
        send({ jsonrpc: "2.0", method: "notifications/progress", params: {
          progressToken: token, progress: n,
          message: childReady ? "Vectorworks is still working…" : "Starting Keystone for Vectorworks (the first start loads Python, up to 2 minutes)…",
        } });
      }, 10000);
    }
    outstanding.set(keyOf(msg.id), entry);
  } else if (failure !== null) {
    return; // notifications and answers have no one to go to
  }
  toServer(t);
}

const rl = readline.createInterface({ input: process.stdin, terminal: false });
rl.on("line", fromClaude);
rl.on("close", () => {
  stdinClosed = true;
  if (!child || failure !== null) process.exit(failure === null ? 0 : 1);
  try { child.stdin.end(); } catch (e) { /* */ }
  setTimeout(() => process.exit(0), 5000).unref();
});

// ---- messages from the server ---------------------------------------------

function fromServer(line) {
  if (!line.trim()) return;
  let msg = null;
  try { msg = JSON.parse(line); } catch (e) { /* pass through */ }
  if (msg && typeof msg === "object" && !Array.isArray(msg) && msg.method === undefined && msg.id !== undefined) {
    const k = keyOf(msg.id);
    if (swallow.has(k)) {
      swallow.delete(k);
      if (k === initKey && !childReady) {
        childReady = true;
        log("server ready");
      }
      return;
    }
    if (outstanding.has(k)) {
      stopBeat(outstanding.get(k));
      outstanding.delete(k);
    }
  }
  try { process.stdout.write(line + "\n"); } catch (e) { log(`write failed: ${e.message}`); }
}

function fail(reason) {
  if (failure !== null) return;
  failure = reason;
  log(`server not available: ${reason}`);
  toChild.length = 0;
  answerOutstanding();
  if (stdinClosed) process.exit(1);
}

// ---- uv and the server ----------------------------------------------------

function isExecutable(p) {
  try {
    fs.accessSync(p, fs.constants.X_OK);
    return fs.statSync(p).isFile();
  } catch (e) {
    return false;
  }
}

function findUv() {
  const candidates = [
    process.env.KEYSTONE_UV,
    path.join(ROOT, "uv", "uv"),
    path.join(HOME, ".local", "bin", "uv"),
    "/opt/homebrew/bin/uv",
    "/usr/local/bin/uv",
    path.join(HOME, ".cargo", "bin", "uv"),
    ...(process.env.PATH || "").split(path.delimiter).map((d) => path.join(d, "uv")),
  ];
  return candidates.find((p) => p && isExecutable(p));
}

function download(url, dest, redirects = 8) {
  return new Promise((resolve, reject) => {
    https.get(url, { headers: { "User-Agent": "keystone-vectorworks" } }, (res) => {
      if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location && redirects > 0) {
        res.resume();
        resolve(download(new URL(res.headers.location, url).toString(), dest, redirects - 1));
        return;
      }
      if (res.statusCode !== 200) {
        res.resume();
        reject(new Error(`HTTP ${res.statusCode} for ${url}`));
        return;
      }
      const out = fs.createWriteStream(dest);
      res.pipe(out);
      out.on("finish", () => out.close(resolve));
      out.on("error", reject);
    }).on("error", reject);
  });
}

async function installUv() {
  if (process.platform !== "darwin") throw new Error("uv not found (install it from https://docs.astral.sh/uv/)");
  const arch = process.arch === "arm64" ? "aarch64" : "x86_64";
  const url = process.env.KEYSTONE_UV_URL ||
    `https://github.com/astral-sh/uv/releases/latest/download/uv-${arch}-apple-darwin.tar.gz`;
  const dir = path.join(ROOT, "uv");
  fs.mkdirSync(dir, { recursive: true });
  const tgz = path.join(dir, "uv.tar.gz");
  log(`uv not found, downloading ${url}`);
  await download(url, tgz);
  execFileSync("/usr/bin/tar", ["-xzf", tgz, "-C", dir, "--strip-components=1"]);
  fs.unlinkSync(tgz);
  const uv = path.join(dir, "uv");
  if (!isExecutable(uv)) throw new Error(`no uv in ${url}`);
  return uv;
}

function startServer(uv) {
  const env = {
    ...process.env,
    // Python and packages managed by uv only: never touch the macOS python3
    // stub (it would ask to install the Xcode tools).
    UV_PYTHON_PREFERENCE: process.env.UV_PYTHON_PREFERENCE || "only-managed",
    // keep the environment outside the extension folder: survives updates
    UV_PROJECT_ENVIRONMENT: process.env.UV_PROJECT_ENVIRONMENT || path.join(ROOT, "venv"),
    PYTHONUNBUFFERED: "1",
  };
  const args = ["run", "--project", BUNDLE, "--no-dev", "--quiet", path.join(BUNDLE, "server", "main.py")];
  log(`starting ${VERSION}: ${uv} ${args.join(" ")}`);
  child = spawn(uv, args, { cwd: BUNDLE, env, stdio: ["pipe", "pipe", "pipe"] });
  child.stdin.on("error", () => { /* server gone: handled on close */ });
  for (const line of toChild.splice(0)) child.stdin.write(line + "\n");
  if (stdinClosed) child.stdin.end();
  readline.createInterface({ input: child.stdout, terminal: false }).on("line", fromServer);
  child.stderr.on("data", (chunk) => {
    const text = chunk.toString();
    stderrTail = (stderrTail + text).slice(-3000);
    try { process.stderr.write(chunk); } catch (e) { /* */ }
    try { fs.appendFileSync(LOG, text); } catch (e) { /* */ }
  });
  child.on("error", (e) => fail(`could not start uv (${uv}): ${e.message}`));
  child.on("close", (code, signal) => {
    const why = `server exited (${signal || code})`;
    if (stdinClosed) { log(why); process.exit(code || 0); }
    if (!childReady) { fail(why); return; }
    // ran and stopped: answer what is open, then let Claude restart us
    failure = why;
    log(why);
    answerOutstanding();
    setTimeout(() => process.exit(code || 1), 500);
  });
}

log(`${NAME} ${VERSION} launcher (node ${process.version}, ${STATIC ? "static handshake" : "no static.json"})`);
(async () => {
  try {
    startServer(findUv() || (await installUv()));
  } catch (e) {
    fail(`launcher failed: ${e.stack || e}`);
  }
})();
