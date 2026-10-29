#!/usr/bin/env node
// Keystone for Vectorworks – Claude Desktop extension launcher.
//
// Claude Desktop runs this with its built-in Node. It finds uv (or downloads
// it once into ~/.vwmcp/uv), then runs the Python server (server/main.py)
// with uv, which provides Python and the dependencies. MCP messages (one
// JSON-RPC message per line) are relayed between Claude and the server.
// If the server can't start, this launcher answers by itself with one tool,
// vw_ping, that reports why. Log: ~/.vwmcp/extension.log
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
let VERSION = "";
try { VERSION = JSON.parse(fs.readFileSync(path.join(BUNDLE, "manifest.json"), "utf8")).version || ""; } catch (e) { /* */ }

function log(msg) {
  const line = `${new Date().toISOString()} ${msg}`;
  try {
    fs.mkdirSync(ROOT, { recursive: true });
    fs.appendFileSync(LOG, line + "\n");
  } catch (e) { /* logging must never stop the server */ }
  try { process.stderr.write(line + "\n"); } catch (e) { /* */ }
}

// ---- uv -------------------------------------------------------------------

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

// ---- relay ----------------------------------------------------------------

let child = null;
let childSpoke = false; // the server has answered at least once
let stdinClosed = false;
let failure = null; // set when the server could not start: fallback mode
let stderrTail = "";
const pending = []; // lines from Claude not yet answered by the server

function send(msg) {
  try { process.stdout.write(JSON.stringify(msg) + "\n"); } catch (e) { log(`write failed: ${e.message}`); }
}

function report() {
  return `${NAME} ${VERSION} konnte den Server nicht starten / could not start the server.\n\n` +
    `${failure}\n\n${stderrTail.trim() ? "Ausgabe / output:\n" + stderrTail.trim() + "\n\n" : ""}` +
    `Log: ${LOG}\nTipp: Claude beenden und neu öffnen (beim ersten Start lädt uv Python und Pakete).`;
}

function fallback(line) {
  let msg;
  try { msg = JSON.parse(line); } catch (e) { return; }
  if (!msg || msg.id === undefined || msg.id === null) return; // notification
  const reply = (result) => send({ jsonrpc: "2.0", id: msg.id, result });
  switch (msg.method) {
    case "initialize":
      reply({
        protocolVersion: (msg.params && msg.params.protocolVersion) || "2025-06-18",
        capabilities: { tools: {} },
        serverInfo: { name: NAME, version: VERSION },
        instructions: `${NAME} could not start. Call vw_ping to see why and tell the user.`,
      });
      break;
    case "tools/list":
      reply({ tools: [{
        name: "vw_ping",
        description: `${NAME} could not start: shows why.`,
        inputSchema: { type: "object", properties: {} },
      }] });
      break;
    case "tools/call":
      reply({ content: [{ type: "text", text: report() }], isError: true });
      break;
    case "ping":
      reply({});
      break;
    default:
      send({ jsonrpc: "2.0", id: msg.id, error: { code: -32601, message: `Method not found: ${msg.method}` } });
  }
}

function enterFallback(reason) {
  if (failure !== null) return;
  failure = reason;
  log(`fallback: ${reason}`);
  for (const line of pending.splice(0)) fallback(line);
  if (stdinClosed) process.exit(1);
}

function finish(reason, code) {
  if (failure !== null) return;
  if (!childSpoke && !stdinClosed) {
    enterFallback(reason);
    return;
  }
  log(reason);
  process.exitCode = code === null || code === undefined ? 1 : code;
  try { process.stdin.destroy(); } catch (e) { /* */ }
  setTimeout(() => process.exit(process.exitCode), 3000).unref();
}

const rl = readline.createInterface({ input: process.stdin, terminal: false });
rl.on("line", (line) => {
  if (failure !== null) { fallback(line); return; }
  if (!childSpoke) pending.push(line);
  if (child && child.stdin.writable) child.stdin.write(line + "\n");
});
rl.on("close", () => {
  stdinClosed = true;
  if (failure !== null || !child) process.exit(failure === null ? 0 : 1);
  try { child.stdin.end(); } catch (e) { /* */ }
});

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
  child.stdin.on("error", () => { /* server gone: handled by close */ });
  for (const line of pending) child.stdin.write(line + "\n");
  if (stdinClosed) child.stdin.end();
  child.stdout.on("data", (chunk) => {
    if (!childSpoke) { childSpoke = true; pending.length = 0; }
    process.stdout.write(chunk);
  });
  child.stderr.on("data", (chunk) => {
    const text = chunk.toString();
    stderrTail = (stderrTail + text).slice(-3000);
    try { process.stderr.write(chunk); } catch (e) { /* */ }
    try { fs.appendFileSync(LOG, text); } catch (e) { /* */ }
  });
  child.on("error", (e) => finish(`could not start uv (${uv}): ${e.message}`, 1));
  child.on("close", (code, signal) => finish(`server exited (${signal || code})`, code));
}

(async () => {
  try {
    startServer(findUv() || (await installUv()));
  } catch (e) {
    enterFallback(`launcher failed: ${e.stack || e}`);
  }
})();
