// Reads the signed-in YouTube account's playlists, as YouTube's own pages do.
// This is the only file that knows YouTube's internal formats; the parsing
// functions are pure so they are tested against saved responses
// (node --test extension/*.test.js). Only playlist and video ids and titles leave the
// browser; the session cookie is used here and never sent anywhere else.

export const ORIGIN = "https://www.youtube.com";
// The parameter YouTube's "Show unavailable videos" menu sends: a playlist
// with taken-down videos listed, so they can be marked rather than missed.
const SHOW_UNAVAILABLE = "wgYCCAA=";
// Liked videos is a record of likes, not something saved to come back to.
const SKIP = new Set(["LL"]);
const MAX_PAGES = 100; // 100 videos a page: far beyond YouTube's 5000 per playlist.

// The authorisation YouTube's web client sends with a signed-in request.
export async function sapisidHash(sapisid, origin, timestamp) {
  const data = new TextEncoder().encode(`${timestamp} ${sapisid} ${origin}`);
  const digest = await crypto.subtle.digest("SHA-1", data);
  const hex = [...new Uint8Array(digest)]
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
  return `${timestamp}_${hex}`;
}

// The JSON object a page assigns after the given marker.
export function embeddedJson(html, marker) {
  const start = html.indexOf(marker);
  if (start < 0) return null;
  let i = html.indexOf("{", start + marker.length);
  if (i < 0) return null;
  const from = i;
  let depth = 0,
    quoted = false;
  for (; i < html.length; i++) {
    const c = html[i];
    if (quoted) {
      if (c === "\\") i++;
      else if (c === '"') quoted = false;
    } else if (c === '"') quoted = true;
    else if (c === "{") depth++;
    else if (c === "}" && --depth === 0) {
      try {
        return JSON.parse(html.slice(from, i + 1));
      } catch {
        return null;
      }
    }
  }
  return null;
}

// What the page says about the session: signed in or not, and the client
// context its own requests carry.
export function pageConfig(html) {
  const config = {};
  const marker = "ytcfg.set(";
  for (
    let at = html.indexOf(marker);
    at >= 0;
    at = html.indexOf(marker, at + 1)
  )
    Object.assign(config, embeddedJson(html.slice(at), marker) || {});
  return {
    loggedIn: !!config.LOGGED_IN,
    context: config.INNERTUBE_CONTEXT || null,
    apiKey: config.INNERTUBE_API_KEY || "",
    sessionIndex: String(config.SESSION_INDEX ?? "0"),
  };
}

// Every object in a response, depth first, in document order.
function* walk(value) {
  if (Array.isArray(value)) for (const v of value) yield* walk(v);
  else if (value && typeof value === "object") {
    yield value;
    for (const v of Object.values(value)) yield* walk(v);
  }
}

export const text = (t) =>
  !t
    ? ""
    : typeof t === "string"
      ? t
      : (t.simpleText ??
        t.content ??
        (t.runs || []).map((r) => r.text).join(""));

// The playlists on the account's Playlists page, old and new layouts alike.
export function parsePlaylists(data) {
  const found = new Map();
  for (const o of walk(data)) {
    const old = o.gridPlaylistRenderer || o.playlistRenderer;
    if (old?.playlistId)
      found.set(old.playlistId, text(old.title) || old.playlistId);
    const lockup = o.lockupViewModel;
    if (
      lockup?.contentId &&
      /PLAYLIST|PODCAST/.test(lockup.contentType || "")
    ) {
      const title = text(lockup.metadata?.lockupMetadataViewModel?.title);
      found.set(lockup.contentId, title || lockup.contentId);
    }
  }
  return [...found]
    .filter(([id]) => !SKIP.has(id))
    .map(([id, title]) => ({ id, title }));
}

// One page of a playlist: its videos in order, and the token for the next.
export function parseVideos(data) {
  const videos = [];
  let continuation = "";
  for (const o of walk(data)) {
    const v = o.playlistVideoRenderer;
    if (v?.videoId)
      videos.push({
        id: v.videoId,
        title: text(v.title),
        channel: text(v.shortBylineText),
        // A taken-down or private video is listed but cannot be played.
        unavailable: v.isPlayable === false,
      });
    const token = o.continuationItemRenderer
      ? [...walk(o.continuationItemRenderer)].find(
          (x) => x.continuationCommand?.token,
        )?.continuationCommand.token
      : "";
    if (token) continuation = token;
  }
  return { videos, continuation };
}

export function playlistTitle(data) {
  for (const o of walk(data)) {
    const title =
      o.playlistMetadataRenderer?.title ||
      text(o.playlistHeaderRenderer?.title) ||
      text(o.pageHeaderViewModel?.title?.dynamicTextViewModel?.text);
    if (title) return title;
  }
  return "";
}

// Everything to sync, given ways to load a page and to call YouTube's API.
// A playlist that fails part way is sent as partial, so nothing in it is
// taken to have left the playlist.
export async function collect({ page, browse }) {
  const html = await page("/feed/playlists");
  const config = pageConfig(html);
  if (!config.loggedIn) throw new Error("Not signed in to YouTube in Chrome.");
  const listed = parsePlaylists(embeddedJson(html, "ytInitialData") || {});
  const playlists = [{ id: "WL", title: "Watch later" }, ...listed].filter(
    (p, i, all) => all.findIndex((q) => q.id === p.id) === i,
  );
  const result = [];
  for (const p of playlists) {
    const playlist = { ...p, videos: [] };
    try {
      let data = await browse(config, {
        browseId: "VL" + p.id,
        params: SHOW_UNAVAILABLE,
      });
      playlist.title = playlistTitle(data) || p.title;
      for (let pages = 0; ; pages++) {
        const { videos, continuation } = parseVideos(data);
        playlist.videos.push(...videos);
        if (!continuation) break;
        if (pages >= MAX_PAGES) throw new Error("Too many pages.");
        data = await browse(config, { continuation });
      }
    } catch (error) {
      playlist.partial = true;
      playlist.error = String(error.message || error);
    }
    result.push(playlist);
  }
  return result;
}

// The browser side: the signed-in session's cookies, and YouTube's own API.
export function browserClient(getCookie) {
  const page = async (path) => {
    const response = await fetch(ORIGIN + path, {
      credentials: "include",
      signal: AbortSignal.timeout(30000),
    });
    if (!response.ok) throw new Error("YouTube returned " + response.status);
    return response.text();
  };
  const browse = async (config, body) => {
    const sapisid =
      (await getCookie("SAPISID")) || (await getCookie("__Secure-3PAPISID"));
    if (!sapisid) throw new Error("Not signed in to YouTube in Chrome.");
    const now = Math.floor(Date.now() / 1000);
    const hashes = [`SAPISIDHASH ${await sapisidHash(sapisid, ORIGIN, now)}`];
    for (const [name, label] of [
      ["__Secure-1PAPISID", "SAPISID1PHASH"],
      ["__Secure-3PAPISID", "SAPISID3PHASH"],
    ]) {
      const value = await getCookie(name);
      if (value)
        hashes.push(`${label} ${await sapisidHash(value, ORIGIN, now)}`);
    }
    const client = config.context?.client || {};
    const response = await fetch(
      ORIGIN +
        "/youtubei/v1/browse?prettyPrint=false" +
        (config.apiKey ? "&key=" + encodeURIComponent(config.apiKey) : ""),
      {
        method: "POST",
        credentials: "include",
        headers: {
          "Content-Type": "application/json",
          Authorization: hashes.join(" "),
          "X-Origin": ORIGIN,
          "X-Goog-AuthUser": config.sessionIndex,
          "X-Youtube-Client-Name": "1",
          "X-Youtube-Client-Version": client.clientVersion || "",
        },
        body: JSON.stringify({ context: config.context, ...body }),
        signal: AbortSignal.timeout(30000),
      },
    );
    if (!response.ok) throw new Error("YouTube returned " + response.status);
    return response.json();
  };
  return { page, browse };
}
