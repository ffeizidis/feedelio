async function settings() {
  return {
    server: "http://localhost:8000",
    token: "",
    sync: true,
    ...(await chrome.storage.local.get(["server", "token", "sync"])),
  };
}
let running = false;
async function sync() {
  if (running) return;
  running = true;
  try {
    const config = await settings();
    if (!config.sync) return;
    const entries = await chrome.readingList.query({});
    const response = await fetch(
      config.server.replace(/\/$/, "") + "/api/actions/chrome_sync",
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(config.token ? { Authorization: "Bearer " + config.token } : {}),
        },
        body: JSON.stringify({ payload: { entries } }),
        signal: AbortSignal.timeout(30000),
      },
    );
    if (!response.ok)
      throw new Error(
        "Feedelio returned " +
          response.status +
          ". Check the server URL and token.",
      );
    const result = await response.json();
    const original = new Map(entries.map((e) => [e.url, e]));
    const current = new Map(
      (await chrome.readingList.query({})).map((e) => [e.url, e]),
    );
    for (const update of result.entries) {
      const item = current.get(update.url);
      // Do not overwrite a Chrome edit made while the sync request was in flight.
      if (
        item &&
        item.lastUpdateTime === original.get(item.url)?.lastUpdateTime &&
        item.hasBeenRead !== update.hasBeenRead
      )
        await chrome.readingList.updateEntry(update);
    }
    await chrome.storage.local.set({
      lastSync: new Date().toISOString(),
      error: "",
    });
    await chrome.action.setBadgeText({ text: "" });
  } catch (error) {
    await chrome.storage.local.set({ error: error.message });
    await chrome.action.setBadgeText({ text: "!" });
    await chrome.action.setBadgeBackgroundColor({ color: "#a14036" });
  } finally {
    running = false;
  }
}
chrome.runtime.onInstalled.addListener(async () => {
  await chrome.alarms.create("sync", { periodInMinutes: 1 });
  await sync();
});
chrome.runtime.onStartup.addListener(async () => {
  await chrome.alarms.create("sync", { periodInMinutes: 1 });
  await sync();
});
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "sync") sync();
});
chrome.readingList.onEntryAdded.addListener(sync);
chrome.readingList.onEntryUpdated.addListener(sync);
chrome.runtime.onMessage.addListener((message, sender, reply) => {
  if (message.type === "sync") {
    sync().then(() => reply({ ok: true }));
    return true;
  }
});
chrome.action.onClicked.addListener(async (tab) => {
  const config = await settings();
  await chrome.tabs.create({
    url:
      config.server.replace(/\/$/, "") +
      "/?subscribe=" +
      encodeURIComponent(tab.url || ""),
  });
});
