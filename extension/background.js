import { ORIGIN, browserClient, collect } from "./youtube.js";

async function settings() {
  return {
    server: "http://localhost:8000",
    token: "",
    sync: true,
    youtube: true,
    ...(await chrome.storage.local.get(["server", "token", "sync", "youtube"])),
  };
}
async function post(config, action, payload) {
  const response = await fetch(
    config.server.replace(/\/$/, "") + "/api/actions/" + action,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(config.token ? { Authorization: "Bearer " + config.token } : {}),
      },
      body: JSON.stringify({ payload }),
      signal: AbortSignal.timeout(30000),
    },
  );
  if (!response.ok)
    throw new Error(
      "Feedelio returned " +
        response.status +
        ". Check the server URL and token.",
    );
  return response.json();
}
let running = false;
async function sync() {
  if (running) return;
  running = true;
  try {
    const config = await settings();
    if (!config.sync) return;
    const entries = await chrome.readingList.query({});
    const result = await post(config, "chrome_sync", { entries });
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
    // Deleted in Feedelio: remove it from Chrome, unless Chrome changed it
    // while the request was in flight.
    for (const url of result.remove || []) {
      const item = current.get(url);
      if (item && item.lastUpdateTime === original.get(url)?.lastUpdateTime)
        await chrome.readingList.removeEntry({ url });
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

// YouTube playlists: read with the signed-in session, every half hour. Only
// ids and titles are sent to Feedelio, and only when something changed.
let youtubeRunning = false;
async function youtubeSync(force = false) {
  if (youtubeRunning) return;
  youtubeRunning = true;
  try {
    const config = await settings();
    if (!config.youtube) return;
    const client = browserClient(
      async (name) => (await chrome.cookies.get({ url: ORIGIN, name }))?.value,
    );
    const playlists = await collect(client);
    const sent = playlists.map(({ error, ...p }) => p);
    const digest = JSON.stringify(sent);
    const { youtubeDigest } = await chrome.storage.local.get("youtubeDigest");
    if (force || digest !== youtubeDigest) {
      await post(config, "youtube_sync", { playlists: sent });
      await chrome.storage.local.set({ youtubeDigest: digest });
    }
    const failed = playlists.filter((p) => p.error);
    await chrome.storage.local.set({
      youtubeSync: new Date().toISOString(),
      youtubeCount: playlists.length,
      youtubeError: failed.length
        ? `Could not read all of ${failed.map((p) => p.title).join(", ")}: ${failed[0].error}`
        : "",
    });
  } catch (error) {
    await chrome.storage.local.set({ youtubeError: error.message });
  } finally {
    youtubeRunning = false;
  }
}
// YouTube's API checks that a request comes from its own pages, as the
// requests YouTube's web client makes do.
async function youtubeOrigin() {
  await chrome.declarativeNetRequest.updateSessionRules({
    removeRuleIds: [1],
    addRules: [
      {
        id: 1,
        priority: 1,
        action: {
          type: "modifyHeaders",
          requestHeaders: [
            { header: "origin", operation: "set", value: ORIGIN },
            { header: "referer", operation: "set", value: ORIGIN + "/" },
          ],
        },
        condition: {
          urlFilter: "|" + ORIGIN + "/youtubei/",
          tabIds: [chrome.tabs.TAB_ID_NONE],
          resourceTypes: ["xmlhttprequest", "other"],
        },
      },
    ],
  });
}

// Chrome 120+ allows alarms every 30 seconds: how soon a read in Feedelio
// reaches Chrome.
const every = { periodInMinutes: 0.5 };
async function begin() {
  await chrome.alarms.create("sync", every);
  await chrome.alarms.create("youtube", { periodInMinutes: 30 });
  await youtubeOrigin();
  await sync();
  await youtubeSync();
}
chrome.runtime.onInstalled.addListener(begin);
chrome.runtime.onStartup.addListener(begin);
chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name === "sync") sync();
  if (alarm.name === "youtube") {
    await youtubeOrigin(); // Session rules end with the browser session.
    youtubeSync();
  }
});
chrome.readingList.onEntryAdded.addListener(sync);
chrome.readingList.onEntryUpdated.addListener(sync);
chrome.readingList.onEntryRemoved.addListener(sync);
chrome.runtime.onMessage.addListener((message, sender, reply) => {
  if (message.type === "sync") {
    sync().then(() => reply({ ok: true }));
    return true;
  }
  if (message.type === "youtube") {
    youtubeOrigin()
      .then(() => youtubeSync(true))
      .then(() => reply({ ok: true }));
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
