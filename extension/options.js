const $ = (id) => document.getElementById(id);
const config = {
  server: "http://localhost:8000",
  token: "",
  sync: true,
  youtube: true,
  ...(await chrome.storage.local.get()),
};
async function showYoutube() {
  const state = await chrome.storage.local.get([
    "youtubeSync",
    "youtubeCount",
    "youtubeError",
  ]);
  $("youtube-status").textContent =
    state.youtubeError ||
    (state.youtubeSync
      ? `YouTube: ${state.youtubeCount} playlists, last read ${new Date(state.youtubeSync).toLocaleString()}`
      : "YouTube: not read yet");
}
showYoutube();
$("youtube-now").addEventListener("click", async () => {
  $("youtube-status").textContent = "Reading your YouTube playlists…";
  await chrome.runtime.sendMessage({ type: "youtube" });
  await showYoutube();
});
$("server").value = config.server;
$("token").value = config.token;
$("sync").checked = config.sync;
$("youtube").checked = config.youtube;
$("status").textContent =
  config.error || "Last sync: " + (config.lastSync || "not yet");
$("settings").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    const server = new URL($("server").value).origin;
    if (!/^https?:/.test(server))
      throw new Error("Use an HTTP or HTTPS server URL.");
    const granted = await chrome.permissions.request({
      origins: [server + "/*"],
    });
    if (!granted) throw new Error("Server access was not granted.");
    await chrome.storage.local.set({
      server,
      token: $("token").value,
      sync: $("sync").checked,
      youtube: $("youtube").checked,
    });
    $("status").textContent = "Syncing…";
    await chrome.runtime.sendMessage({ type: "sync" });
    const state = await chrome.storage.local.get(["lastSync", "error"]);
    $("status").textContent =
      state.error || "Saved. Last sync: " + (state.lastSync || "sync disabled");
  } catch (error) {
    $("status").textContent = error.message;
  }
});
