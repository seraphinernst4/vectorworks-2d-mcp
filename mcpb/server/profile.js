// The user's profile block for the server instructions (Keystone 0.7).
//
// The launcher answers `initialize` from static.json before the Python
// server runs, so the instructions there hold the default profile. This puts
// the user's own profile (~/.vwmcp/profile.json) in its place, built from the
// rule table the build copies from vwmcp/profile.py (static.json "profile")
// - the same text Python's profile.instructions_block() produces.
"use strict";

const fs = require("fs");
const path = require("path");

function g(x) {
  // Python's "%g" for the value ranges the profile allows
  return String(parseFloat(Number(x).toPrecision(6)));
}

function has(o, k) {
  return o !== null && typeof o === "object" && Object.prototype.hasOwnProperty.call(o, k);
}

function load(table, stored) {
  const prof = {};
  for (const k of table.order) {
    const d = table.settings[k];
    let v = d.default;
    if (has(stored, k)) {
      const s = stored[k];
      if (d.choices) {
        if (typeof s === "string" && has(d.choices, s)) v = s;
      } else if (d.type === "list") {
        const arr = typeof s === "string" ? [s] : s;
        if (Array.isArray(arr) && arr.every((x) => typeof x === "string" && x.trim())) v = arr.map((x) => x.trim());
      } else if (typeof s === "number" && isFinite(s) && s >= d.min && s <= d.max) {
        v = d.type === "int" ? Math.trunc(s) : s;
      }
    }
    prof[k] = v;
  }
  return prof;
}

function block(table, prof) {
  const lines = [];
  for (const k of table.order) {
    const d = table.settings[k];
    const v = prof[k];
    let line = null;
    if (d.choices) line = d.choices[v];
    else if (d.type === "list") line = v.length ? d.en + v.map((r, i) => `\n  ${i + 1}. ${r}`).join("") : null;
    else if (!v && d.en_off) line = d.en_off;
    else line = d.en.split("{v100}").join(g(v * 100)).split("{v}").join(g(v));
    if (line) lines.push("- " + line);
  }
  return lines.join("\n");
}

function readStored(root) {
  try {
    const data = JSON.parse(fs.readFileSync(path.join(root, "profile.json"), "utf8"));
    return data && typeof data === "object" && !Array.isArray(data) ? data : {};
  } catch (e) {
    return {};
  }
}

// instructions with the default profile block replaced by the user's
function withProfile(instructions, profile, root) {
  if (!instructions || !profile || !profile.table || !profile.defaultBlock) return instructions;
  if (!instructions.includes(profile.defaultBlock)) return instructions;
  const mine = block(profile.table, load(profile.table, readStored(root)));
  return instructions.replace(profile.defaultBlock, () => mine);
}

module.exports = { block, load, readStored, withProfile };
