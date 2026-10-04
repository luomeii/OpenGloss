// DEPRECATED (2026-09, R1 experiment): CSS Custom Highlight registration from a
// content script's isolated world does NOT paint (registry is per-JS-world).
// The working implementation now lives in sw.js as paeRegisterHighlights(), which
// is injected into the MAIN world via chrome.scripting.executeScript({world:'MAIN'}).
// Kept for reference only; not loaded by manifest.json.
(function (global) {
  'use strict';