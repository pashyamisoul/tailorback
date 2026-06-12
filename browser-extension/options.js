"use strict";

const DEFAULT_APP_URL = "http://127.0.0.1:5000/";
const input = document.getElementById("appUrl");
const saved = document.getElementById("saved");

chrome.storage.sync.get({ appUrl: DEFAULT_APP_URL }, (v) => {
  input.value = (v && v.appUrl) || DEFAULT_APP_URL;
});

document.getElementById("save").addEventListener("click", () => {
  let url = input.value.trim() || DEFAULT_APP_URL;
  if (!/^https?:\/\//i.test(url)) url = "https://" + url;
  chrome.storage.sync.set({ appUrl: url }, () => {
    input.value = url;
    saved.textContent = "Saved ✓";
    setTimeout(() => { saved.textContent = ""; }, 1800);
  });
});
