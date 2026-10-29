#!/usr/bin/env node
// Keystone for Vectorworks – Claude Desktop extension launcher.
//
// Claude Desktop runs this with its built-in Node. It finds uv (or downloads
// it once into ~/.vwmcp/uv), then runs the Python server (server/main.py)
// with uv, which provides Python and the dependencies. stdin/stdout are
// handed straight to the server (MCP over stdio). Log: ~/.vwmcp/extension.log
"use strict";

const { spawn, execFileSync } = require("child_process");
const fs = require("fs");
const https = require("https");
const os = require("os");
const path = require("path");

const HOME = os.homedir();
const ROOT = process.env.VWMCP_HOME || path.join(HOME, ".vwmcp");
const LOG = path.join(ROOT, "extension.log");
const BUNDLE = path.resolve(__dirname, "..");

function log(msg) {
  const line = `${new Date().toISOString()} ${msg}`;
  try {
    fs.mkdirSync(ROOT, { recursive: true });
    fs.appendFileSync(LOG, line + "\n");
  } catch (e) { /* logging must never stop the server */ }
  process.stderr.write(line + "\n");
}

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

async function main() {
  let uv = findUv();
  if (!uv) uv = await installUv();
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
  log(`starting: ${uv} ${args.join(" ")}`);
  const child = spawn(uv, args, { cwd: BUNDLE, env, stdio: ["inherit", "inherit", "pipe"] });
  child.stderr.on("data", (chunk) => {
    process.stderr.write(chunk);
    try { fs.appendFileSync(LOG, chunk); } catch (e) { /* ignore */ }
  });
  for (const sig of ["SIGTERM", "SIGINT", "SIGHUP"]) {
    process.on(sig, () => child.kill(sig));
  }
  child.on("error", (e) => { log(`could not start uv: ${e.message}`); process.exit(1); });
  child.on("exit", (code, signal) => {
    log(`server exited (${signal || code})`);
    process.exit(code === null ? 1 : code);
  });
}

main().catch((e) => {
  log(`launcher failed: ${e.stack || e}`);
  process.exit(1);
});
