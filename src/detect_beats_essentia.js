"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");

function loadEssentia() {
  const root = path.resolve(__dirname, "..");
  const wasmPath = path.join(root, "vendor", "essentia-wasm.umd.js");
  const corePath = path.join(root, "vendor", "essentia.js-core.umd.js");
  const wasmModule = require(wasmPath);
  const Essentia = require(corePath);
  return new Essentia(wasmModule.EssentiaWASM || wasmModule);
}

function littleEndianSamples(buffer) {
  const length = buffer.length - (buffer.length % 4);
  if (length === 0) {
    return new Float32Array(0);
  }

  if (os.endianness() === "LE") {
    const aligned = new Uint8Array(length);
    aligned.set(buffer.subarray(0, length));
    return new Float32Array(aligned.buffer);
  }

  const samples = new Float32Array(length / 4);
  for (let index = 0; index < samples.length; index++) {
    samples[index] = buffer.readFloatLE(index * 4);
  }
  return samples;
}

function main() {
  const samples = littleEndianSamples(fs.readFileSync(0));
  const essentia = loadEssentia();
  const result = essentia.RhythmExtractor2013(
    essentia.arrayToVector(samples),
    208,
    "multifeature",
    40,
  );

  process.stdout.write(JSON.stringify({
    bpm: result.bpm,
    beats: Array.from(essentia.vectorToArray(result.ticks)),
    confidence: result.confidence,
  }));
}

try {
  main();
} catch (error) {
  console.error(error && error.stack ? error.stack : String(error));
  process.exit(1);
}
