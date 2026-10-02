// node --test extension/*.test.js
// Responses below follow the shapes YouTube's web client receives, trimmed to
// what the parser reads, with made-up ids and titles.
import assert from "node:assert/strict";
import test from "node:test";
import {
  collect,
  embeddedJson,
  pageConfig,
  parsePlaylists,
  parseVideos,
  sapisidHash,
} from "./youtube.js";

const playlistsPage = (loggedIn = true) => `<!doctype html><html><head>
<script>ytcfg.set({"LOGGED_IN": ${loggedIn}, "SESSION_INDEX": 0});</script>
<script>ytcfg.set({"INNERTUBE_API_KEY": "key", "INNERTUBE_CONTEXT": {"client": {"clientName": "WEB", "clientVersion": "2.20260901.00.00"}}});</script>
</head><body><script>var ytInitialData = ${JSON.stringify({
  contents: {
    twoColumnBrowseResultsRenderer: {
      tabs: [
        {
          tabRenderer: {
            content: {
              richGridRenderer: {
                contents: [
                  {
                    richItemRenderer: {
                      content: {
                        lockupViewModel: {
                          contentId: "PLbread0001",
                          contentType: "LOCKUP_CONTENT_TYPE_PLAYLIST",
                          metadata: {
                            lockupMetadataViewModel: {
                              title: { content: 'Bread {and} "rolls"' },
                            },
                          },
                        },
                      },
                    },
                  },
                  {
                    richItemRenderer: {
                      content: {
                        lockupViewModel: {
                          contentId: "LL",
                          contentType: "LOCKUP_CONTENT_TYPE_PLAYLIST",
                          metadata: {
                            lockupMetadataViewModel: {
                              title: { content: "Liked videos" },
                            },
                          },
                        },
                      },
                    },
                  },
                  {
                    gridPlaylistRenderer: {
                      playlistId: "PLolder0001",
                      title: { runs: [{ text: "Older " }, { text: "layout" }] },
                    },
                  },
                ],
              },
            },
          },
        },
      ],
    },
  },
})};</script></body></html>`;

const video = (id, extra = {}) => ({
  playlistVideoRenderer: {
    videoId: id,
    title: { runs: [{ text: "Video " + id }] },
    shortBylineText: { runs: [{ text: "A channel" }] },
    lengthText: { simpleText: "12:05" },
    isPlayable: true,
    ...extra,
  },
});
const more = (token) => ({
  continuationItemRenderer: {
    continuationEndpoint: {
      commandExecutorCommand: {
        commands: [
          {
            continuationCommand: {
              token,
              request: "CONTINUATION_REQUEST_TYPE_BROWSE",
            },
          },
        ],
      },
    },
  },
});
const firstPage = (title, items) => ({
  metadata: { playlistMetadataRenderer: { title } },
  contents: {
    twoColumnBrowseResultsRenderer: {
      tabs: [
        {
          tabRenderer: {
            content: {
              sectionListRenderer: {
                contents: [
                  {
                    itemSectionRenderer: {
                      contents: [
                        { playlistVideoListRenderer: { contents: items } },
                      ],
                    },
                  },
                ],
              },
            },
          },
        },
      ],
    },
  },
});
const nextPage = (items) => ({
  onResponseReceivedActions: [
    { appendContinuationItemsAction: { continuationItems: items } },
  ],
});

test("the authorisation matches YouTube's web client", async () => {
  assert.equal(
    await sapisidHash("abcSAPISID", "https://www.youtube.com", 1700000000),
    "1700000000_b4d59c573190b5106be035dd4b3e304f7edd9c46",
  );
});

test("the page's settings and data are read, braces in strings and all", () => {
  const html = playlistsPage();
  assert.deepEqual(pageConfig(html), {
    loggedIn: true,
    context: {
      client: { clientName: "WEB", clientVersion: "2.20260901.00.00" },
    },
    apiKey: "key",
    sessionIndex: "0",
  });
  assert.equal(pageConfig(playlistsPage(false)).loggedIn, false);
  assert.equal(embeddedJson("var x = {", "x"), null);
});

test("playlists come from both layouts, without Liked videos", () => {
  const data = embeddedJson(playlistsPage(), "ytInitialData");
  assert.deepEqual(parsePlaylists(data), [
    { id: "PLbread0001", title: 'Bread {and} "rolls"' },
    { id: "PLolder0001", title: "Older layout" },
  ]);
});

test("a page of videos lists them in order and finds the next page", () => {
  const page = firstPage("Bread", [
    video("aaaaaaaaaaa"),
    video("bbbbbbbbbbb", {
      title: { simpleText: "[Deleted video]" },
      shortBylineText: undefined,
      lengthText: undefined,
      isPlayable: false,
    }),
    more("next-token"),
  ]);
  assert.deepEqual(parseVideos(page), {
    videos: [
      {
        id: "aaaaaaaaaaa",
        title: "Video aaaaaaaaaaa",
        channel: "A channel",
        unavailable: false,
      },
      {
        id: "bbbbbbbbbbb",
        title: "[Deleted video]",
        channel: "",
        unavailable: true,
      },
    ],
    continuation: "next-token",
  });
  assert.equal(parseVideos(nextPage([video("ccccccccccc")])).continuation, "");
});

test("everything is collected, Watch Later first, through every page", async () => {
  const calls = [];
  const pages = {
    VLWL: firstPage("Watch later", [video("aaaaaaaaaaa"), more("wl-2")]),
    "wl-2": nextPage([video("bbbbbbbbbbb")]),
    VLPLbread0001: firstPage("Bread and rolls", [video("ccccccccccc")]),
  };
  const result = await collect({
    page: async (path) => {
      assert.equal(path, "/feed/playlists");
      return playlistsPage();
    },
    browse: async (config, body) => {
      assert.equal(config.apiKey, "key");
      calls.push(body);
      if (body.browseId === "VLPLolder0001")
        throw new Error("YouTube returned 500");
      return pages[body.browseId || body.continuation];
    },
  });
  assert.deepEqual(calls[0], { browseId: "VLWL", params: "wgYCCAA=" });
  assert.deepEqual(
    result.map((p) => [p.id, p.title, p.videos.map((v) => v.id), !!p.partial]),
    [
      ["WL", "Watch later", ["aaaaaaaaaaa", "bbbbbbbbbbb"], false],
      ["PLbread0001", "Bread and rolls", ["ccccccccccc"], false],
      ["PLolder0001", "Older layout", [], true],
    ],
  );
});

test("signed out, nothing is collected", async () => {
  await assert.rejects(
    collect({ page: async () => playlistsPage(false), browse: assert.fail }),
    /Not signed in/,
  );
});
