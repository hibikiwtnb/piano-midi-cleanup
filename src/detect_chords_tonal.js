"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

function loadTonal() {
  const root = path.resolve(__dirname, "..");
  const tonalPath = path.join(root, "vendor", "tonal.min.js");
  const code = fs.readFileSync(tonalPath, "utf8");
  const sandbox = {};
  vm.runInNewContext(code, sandbox, { filename: tonalPath });
  if (!sandbox.Tonal || !sandbox.Tonal.Chord || !sandbox.Tonal.Chord.detect) {
    throw new Error("Tonal.Chord.detect is unavailable.");
  }
  return sandbox.Tonal;
}

function normalizeChordName(name) {
  return name.replace(/M($|(?=\/))/g, "");
}

function uniqueNotes(notes) {
  return Array.from(new Set(notes.filter((note) => typeof note === "string" && note)));
}

function main() {
  const input = fs.readFileSync(0, "utf8");
  const payload = JSON.parse(input || "{}");
  const noteGroups = Array.isArray(payload.noteGroups) ? payload.noteGroups : [];
  const maxCandidates = Number.isInteger(payload.maxCandidates) ? payload.maxCandidates : 3;
  const Tonal = loadTonal();

  const results = noteGroups.map((notes) => {
    const detected = Tonal.Chord.detect(uniqueNotes(notes), { assumePerfectFifth: true });
    return detected.map(normalizeChordName).slice(0, maxCandidates);
  });

  process.stdout.write(JSON.stringify(results));
}

try {
  main();
} catch (error) {
  console.error(error && error.stack ? error.stack : String(error));
  process.exit(1);
}
