import React, { useEffect, useRef, useState, useDeferredValue } from "react";
import { createRoot } from "react-dom/client";
import {
  QueryClient,
  QueryClientProvider,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import DOMPurify from "dompurify";
import {
  Rss,
  Plus,
  Search,
  Star,
  Clock3,
  ChevronDown,
  ChevronRight,
  ChevronLeft,
  ArrowUpDown,
  Check,
  CheckCheck,
  Settings2,
  Folder,
  X,
  ExternalLink,
  BookOpen,
  RefreshCw,
  Sun,
  Moon,
  Monitor,
  Type,
  ArrowUpRight,
  Trash2,
  Download,
  Undo2,
  Circle,
  CheckCircle2,
  MoreHorizontal,
  Headphones,
  FileText,
  ShieldCheck,
  SlidersHorizontal,
  Inbox,
  AlertCircle,
  Leaf,
  Lock,
  Smartphone,
  Video,
  Podcast,
  Eye,
  EyeOff,
  Bookmark,
  Image,
  Loader2,
  Pencil,
  Filter,
} from "lucide-react";
import "./style.css";
import "@fontsource/italianno/latin-400.css";
import "@fontsource-variable/newsreader/wght.css";
import {
  ResizeHandle,
  Switch,
  Highlight,
  KeyboardGuide,
  AppearanceSettings,
  FolderSheet,
  FolderManager,
  FolderSelect,
  RulesManager,
  RuleSheet,
  Select,
  folderOptions,
  useSubmit,
  feedClass,
} from "./redesign.jsx";

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } },
});
// A CSP change needs a new document. Preserve the reading position for that
// one reload, without persisting article bodies or credentials in the browser.
let resume = {};
try {
  resume =
    JSON.parse(sessionStorage.getItem("feedelio-policy-resume") || "{}") || {};
  sessionStorage.removeItem("feedelio-policy-resume");
} catch {
  /* An unavailable session store only loses the reading position. */
}
// Width of the always-visible tray at the far left, matching --tray-width.
const TRAY_WIDTH = 64;
// Chrome's Reading List arrives as one hidden feed; its articles show under
// the website they are on, in their own view.
const READING_LIST = "chrome";
// The address names the page, so a reload or a shared link lands on the same
// stream or article: /f/<feed>, /d/<folder>, /starred, /bookmarks, /reading,
// /history, /a/<article>; a bookmarked feed or folder is /bookmarks/f/<feed> or /bookmarks/d/<folder>.
const LIBRARY_TABS = [
  "feeds",
  "folders",
  "appearance",
  "keyboard",
  "data",
  "rules",
  "downloads",
  "statistics",
];
function readLocation() {
  const [, kind, id, feed] = location.pathname.split("/");
  if (kind === "library")
    return {
      modal: LIBRARY_TABS.includes(id) ? id : "feeds",
      manageFeed: id === "feeds" && feed ? feed : null,
      manageFolder: id === "folders" && feed ? feed : null,
    };
  if (kind === "a" && id) return { selected: id };
  if (kind === "f" && id) return { view: "all", scope: { feed_id: id } };
  if (kind === "d" && id) return { view: "all", scope: { folder_id: id } };
  if (kind === "bookmarks" && feed && (id === "f" || id === "d"))
    return {
      view: kind,
      scope: id === "f" ? { feed_id: feed } : { folder_id: feed },
    };
  if (["starred", "bookmarks", "reading", "history"].includes(kind))
    return { view: kind };
  return {};
}
function pageUrl(selected, view, scope) {
  if (selected) return "/a/" + selected;
  const within = view === "bookmarks" ? "/bookmarks" : "";
  if (scope.feed_id) return within + "/f/" + scope.feed_id;
  if (scope.folder_id) return within + "/d/" + scope.folder_id;
  return view === "all" ? "/" : "/" + view;
}
const start = Object.keys(resume).length ? resume : readLocation();
async function api(path, body) {
  const response = await fetch(
    "/api/" + path,
    body === undefined
      ? {}
      : {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        },
  );
  let data;
  try {
    data = await response.json();
  } catch {
    throw new Error("The server returned an unexpected response.");
  }
  if (!response.ok) {
    const error = new Error(
      typeof data.detail === "string"
        ? data.detail
        : "Please check your input.",
    );
    error.status = response.status;
    throw error;
  }
  return data;
}
const cmd = (name, payload = {}) => api("actions/" + name, { payload });
// A publisher's page always opens disowned, in its own tab.
const openExternal = (url) => window.open(url, "_blank", "noopener,noreferrer");
// Middle click opens a new tab, like on a link: the original for a title, the
// feed for a feed name, the website for a reading-list page.
const middleClick = (url) => ({
  onMouseDown: (e) => e.button === 1 && e.preventDefault(),
  onAuxClick: (e) => {
    if (e.button !== 1 || !url) return;
    e.preventDefault();
    openExternal(url);
  },
});
// The feed list's scrollbar shows only while it is being scrolled.
const showScrollbar = (e) => {
  const el = e.currentTarget;
  el.classList.add("scrolling");
  clearTimeout(el.scrollingTimer);
  el.scrollingTimer = setTimeout(() => el.classList.remove("scrolling"), 900);
};
// Queue an update: one feed when given, the whole library otherwise.
const refreshJob = (feedId) => [
  "enqueue",
  { kind: "refresh", payload: feedId ? { feed_id: feedId, manual: true } : {} },
  "Update queued",
];
const extractJob = (articleId) => [
  "enqueue",
  { kind: "extract", payload: { article_id: articleId, asked: true } },
  "Full article extraction queued",
];
// A comma-separated field as a list of tags.
const parseTags = (text) =>
  (text || "")
    .split(",")
    .map((t) => t.trim())
    .filter(Boolean);
// Age in the list: now, 1m, 10m, 2h, 3d, 3w, 7mn, 2y.
const ago = (value) => {
  const minutes = Math.max(0, (Date.now() - new Date(value).getTime()) / 60000);
  if (minutes < 1) return "now";
  if (minutes < 60) return `${Math.floor(minutes)}m`;
  if (minutes < 1440) return `${Math.floor(minutes / 60)}h`;
  if (minutes < 10080) return `${Math.floor(minutes / 1440)}d`;
  if (minutes < 43800) return `${Math.floor(minutes / 10080)}w`;
  if (minutes < 525600) return `${Math.floor(minutes / 43800)}mn`;
  return `${Math.floor(minutes / 525600)}y`;
};
const fullDate = (value) =>
  new Date(value).toLocaleDateString(undefined, {
    month: "long",
    day: "numeric",
    year: "numeric",
  });
// A tooltip over a title that already fits is noise, so the attribute is
// attached on hover, when the browser can say whether the pane cut it off.
const fullOnHover = (e) => {
  const el = e.currentTarget;
  if (el.scrollWidth > el.clientWidth) el.title = el.textContent;
  else el.removeAttribute("title");
};
// Each open layer (article, library, feed editor) owns one history entry, so
// Back closes the top layer. The stack is the source of truth; history.go on
// our own closes is absorbed by the skip counter in the popstate handler.
function useLayer(layers, name, open, onClose) {
  layers.current.close[name] = onClose;
  useEffect(() => {
    const state = layers.current;
    if (open && !state.stack.includes(name)) {
      state.stack.push(name);
      history.pushState({ feedelio: name }, "");
    } else if (!open) closeLayer(state, name);
  }, [open]);
  useEffect(() => () => closeLayer(layers.current, name), []);
}
function closeLayer(state, name) {
  const index = state.stack.indexOf(name);
  if (index < 0) return;
  const depth = state.stack.length - index;
  state.stack.splice(index);
  state.skip += depth;
  history.go(-depth);
}
function clock(seconds) {
  const h = Math.floor(seconds / 3600),
    m = Math.floor((seconds % 3600) / 60),
    s = seconds % 60;
  return (h ? [h, m, s] : [m, s])
    .map((n, i) => (i ? String(n).padStart(2, "0") : n))
    .join(":");
}
function fileSize(bytes) {
  const units = ["B", "KB", "MB", "GB"];
  let i = 0;
  for (; bytes >= 1024 && i < units.length - 1; i++) bytes /= 1024;
  return `${bytes < 10 && i ? bytes.toFixed(1) : Math.round(bytes)} ${units[i]}`;
}
// The same ages, for a feed that may never have been checked.
const elapsed = (value) => (value ? ago(value) : "never");
// What the server says about a feed, in the words the library uses. The
// classification itself lives in Core.feeds(): one verdict, one threshold.
function feedIssue(f) {
  if (f.health === "error")
    return {
      kind: "error",
      label: `failed ${elapsed(f.checked)}`,
      note: `${f.failures} failed fetches: ${f.error}`,
    };
  if (f.health === "stale")
    return {
      kind: "stale",
      label: `stale ${elapsed(f.last_article || f.created)}`,
      note: "No new items in two weeks or more",
    };
  if (f.health === "partial")
    return {
      kind: "partial",
      label: "partial",
      note: `${f.partial_items} articles are paywalled or archive previews; full text may need a subscription`,
    };
  return null;
}
// What a stream of articles is called, in the heading and in the tab.
const VIEW_TITLES = {
  all: "All articles",
  starred: "Saved for later",
  bookmarks: "Bookmarked feeds",
  reading: "Reading list",
  history: "Recently read",
};
const streamTitle = (data, view, scope, q) =>
  q
    ? "Search results"
    : data?.feeds.find((f) => f.id === scope.feed_id)?.title ||
      data?.folders.find((f) => f.id === scope.folder_id)?.name ||
      VIEW_TITLES[view];
const feedStatuses = {
  all: "All feeds",
  healthy: "Healthy",
  pending: "Not checked yet",
  error: "Failing",
  stale: "Stale",
  partial: "Partial",
};
// Small marks in the article list: what kind of item this is before opening it.
function ArticleMarks({ item }) {
  const url = item.url || "";
  const video =
    /^https?:\/\/(www\.|m\.)?(youtube\.com\/(watch|shorts|embed)|youtu\.be\/)/.test(
      url,
    );
  const short =
    item.tags.includes("short") || /youtube\.com\/shorts\//.test(url);
  const paywall = item.tags.includes("paywall");
  if (!video && !item.audio && !paywall) return null;
  return (
    <span className="row-marks">
      {video && short && <Smartphone size={15} aria-label="YouTube Short" />}
      {video && !short && <Video size={15} aria-label="Video" />}
      {!video && !!item.audio && <Podcast size={15} aria-label="Podcast" />}
      {paywall && <Lock size={15} aria-label="Paywalled" />}
    </span>
  );
}
// The read dot and the star, in the list and in the reader.
function ReadStar({ item, run, labels }) {
  return (
    <>
      <button
        className="read-toggle"
        aria-label={item.read ? labels.unread : labels.read}
        onClick={() =>
          run("change_articles", { ids: [item.id], read: !item.read })
        }
      >
        <span className={"read-circle " + (item.read ? "is-read" : "")} />
      </button>
      <button
        className={"star-toggle " + (item.starred ? "starred" : "")}
        aria-label={item.starred ? labels.unstar : labels.star}
        aria-pressed={!!item.starred}
        onClick={() =>
          run("change_articles", { ids: [item.id], starred: !item.starred })
        }
      >
        ★
      </button>
    </>
  );
}
// A stream heading that opens the settings of what it names.
function HeadingLink({ title, label, onClick, children }) {
  return (
    <h1>
      <button
        className="heading-link"
        title={title}
        aria-label={label}
        onClick={onClick}
      >
        {children}
      </button>
    </h1>
  );
}
// A failed job can always be put back in the queue as it was.
function RetryJob({ job, act, submit }) {
  if (job.status !== "failed") return null;
  return (
    <button
      onClick={() =>
        submit(() =>
          act(
            "enqueue",
            { kind: job.kind, payload: job.payload },
            "Job queued again",
          ),
        )
      }
    >
      Retry
    </button>
  );
}
function IconButton({ icon: Icon, label, ...props }) {
  return (
    <button className="icon-button" title={label} aria-label={label} {...props}>
      <Icon size={18} />
    </button>
  );
}
// Queues a refresh of one feed (or the whole library) and stays busy until the
// worker has run it, so a second click cannot pile up duplicate jobs.
function UpdateNow({ data, act, feedId, checked }) {
  const pending = (data.jobs || []).some(
    (j) =>
      j.kind === "refresh" &&
      (j.status === "queued" || j.status === "running") &&
      (!feedId || !j.payload.feed_id || j.payload.feed_id === feedId),
  );
  return (
    <button
      type="button"
      className="update-now"
      disabled={pending}
      title={checked ? "Last checked " + ago(checked) + " ago" : undefined}
      onClick={() => act(...refreshJob(feedId))}
    >
      <RefreshCw size={16} className={pending ? "spin" : ""} />
      {pending ? "Updating…" : "Update now"}
    </button>
  );
}
function Field({ label, children, hint }) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
      {hint && <small>{hint}</small>}
    </label>
  );
}
function Modal({ title, subtitle, onClose, children, wide = false }) {
  const ref = useRef(null);
  useEffect(() => {
    ref.current.showModal();
  }, []);
  return (
    <dialog
      ref={ref}
      className={wide ? "wide" : ""}
      onCancel={(e) => {
        e.preventDefault();
        e.stopPropagation();
        onClose();
      }}
      onClick={(e) => {
        if (e.target === ref.current) onClose();
      }}
    >
      <header className="dialog-header">
        <div>
          <h2>{title}</h2>
          {subtitle && <p>{subtitle}</p>}
        </div>
        <IconButton icon={X} label="Close dialog" onClick={onClose} />
      </header>
      {children}
    </dialog>
  );
}

// One sentence about an archive walk, wherever it is shown.
function archiveSummary(job) {
  const status = {
    queued: "in progress",
    running: "in progress",
    paused: "paused",
    failed: "failed",
    done: "complete",
  }[job.status];
  const parts = [
    `Archive ${status || job.status}`,
    `${job.result?.pages || 0} pages`,
    `${job.result?.articles || 0} older articles`,
  ];
  if (job.result?.previews)
    parts.push(`${job.result.previews} available as preview only`);
  if (job.status === "queued")
    parts.push(
      job.result?.not_before
        ? "next page after " +
            new Date(job.result.not_before).toLocaleTimeString()
        : "one page per minute",
    );
  return parts.join(" · ");
}
function BackfillStatus({ jobs, feeds, scope, act }) {
  const latest = [
    ...new Map([...jobs].reverse().map((j) => [j.payload.feed_id, j])).values(),
  ];
  const visible = latest.filter(
    (j) =>
      feeds.some((f) => f.id === j.payload.feed_id) &&
      (!scope.feed_id || j.payload.feed_id === scope.feed_id),
  );
  if (!visible.length) return null;
  return (
    <div className="backfill-status" aria-label="Archive progress">
      {visible.map((j) => (
        <div key={j.id}>
          <strong>
            {feeds.find((f) => f.id === j.payload.feed_id)?.title}:{" "}
          </strong>
          {archiveSummary(j)}
          {["queued", "running", "paused", "failed"].includes(j.status) && (
            <button
              type="button"
              onClick={() =>
                act("control_backfill", {
                  id: j.id,
                  action: ["paused", "failed"].includes(j.status)
                    ? "resume"
                    : "pause",
                })
              }
            >
              {["paused", "failed"].includes(j.status)
                ? "Resume archive"
                : "Pause archive"}
            </button>
          )}
          {j.error && <p className="error-color">{j.error}</p>}
          {j.result?.message && <p>{j.result.message}</p>}
        </div>
      ))}
    </div>
  );
}

function App() {
  const cache = useQueryClient();
  const overview = useQuery({
    queryKey: ["overview"],
    queryFn: () => api("overview"),
    refetchInterval: 10000,
  });
  const [view, setView] = useState(start.view || "all"),
    [scope, setScope] = useState(start.scope || {}),
    [q, setQ] = useState(start.q || ""),
    [offset, setOffset] = useState(start.offset || 0);
  const [selected, setSelected] = useState(start.selected || null),
    [collapsed, setCollapsed] = useState({}),
    [modal, setModal] = useState(
      new URLSearchParams(location.search).has("subscribe")
        ? "subscribe"
        : start.modal || null,
    );
  const [manageFeed, setManageFeed] = useState(start.manageFeed || null),
    [manageFolder, setManageFolder] = useState(start.manageFolder || null);
  const [toast, setToast] = useState(null),
    [busy, setBusy] = useState(0),
    [loadImages, setLoadImages] = useState(false),
    [reloadPolicy, setReloadPolicy] = useState(false);
  const [token, setToken] = useState("");
  const [paneWidth, setPaneWidth] = useState(null);
  const [listWidth, setListWidth] = useState(null),
    [searchOpen, setSearchOpen] = useState(!!start.q),
    [titlesOnly, setTitlesOnly] = useState(false),
    [focused, setFocused] = useState(null),
    [viewport, setViewport] = useState(window.innerWidth);
  const goKey = useRef(0);
  useEffect(() => {
    const resize = () => setViewport(window.innerWidth);
    window.addEventListener("resize", resize);
    return () => window.removeEventListener("resize", resize);
  }, []);
  // The reader and the library each push a history entry, so Back (keyboard,
  // toolbar or a mouse side button) closes them instead of leaving Feedelio.
  const layers = useRef({ stack: [], skip: 0, close: {}, url: "/" });
  // Folders stay collapsed or open the way they were left, across visits.
  const collapsedLoaded = useRef(false);
  useEffect(() => {
    if (overview.data && !collapsedLoaded.current) {
      collapsedLoaded.current = true;
      setCollapsed(
        Object.fromEntries(
          (overview.data.settings.collapsed_folders || []).map((id) => [
            "nav" + id,
            true,
          ]),
        ),
      );
    }
  }, [overview.data]);
  const toggleFolder = (id) => {
    const next = { ...collapsed, ["nav" + id]: !collapsed["nav" + id] };
    setCollapsed(next);
    pref(
      "collapsed_folders",
      Object.keys(next)
        .filter((k) => next[k])
        .map((k) => k.slice(3)),
    );
  };
  useLayer(layers, "article", !!selected, () => setSelected(null));
  useLayer(layers, "modal", !!modal, () => setModal(null));
  useEffect(() => {
    const pop = () => {
      const state = layers.current;
      if (state.skip) {
        state.skip--;
        // Landed on an entry we named earlier, possibly under the closed layer's
        // own address after a reload: name it for what is shown now.
        if (location.pathname + location.search !== state.url)
          history.replaceState(history.state, "", state.url);
      } else state.close[state.stack.pop()]?.();
    };
    window.addEventListener("popstate", pop);
    return () => window.removeEventListener("popstate", pop);
  }, []);
  // After the layer above has pushed its entry, name it. The library names
  // its own sections while it is open.
  useEffect(() => {
    if (LIBRARY_TABS.includes(modal)) return;
    const url = pageUrl(selected, view, scope) + location.search;
    layers.current.url = url;
    if (location.pathname + location.search !== url)
      history.replaceState(history.state, "", url);
  }, [selected, view, scope, modal]);
  const searchRef = useRef(null),
    readingRef = useRef(null),
    navigation = useRef({ key: null, items: [], index: -1 });
  const data = overview.data,
    settings = data?.settings || {};
  const deferredQ = useDeferredValue(q);
  const params = {
    view,
    ...scope,
    q: deferredQ,
    sort: settings.sort || "newest",
    unread: !!settings.hide_read,
    deduplicate: settings.deduplicate !== false,
    titles_only: titlesOnly,
    offset,
  };
  const articles = useQuery({
    queryKey: ["articles", params],
    queryFn: () => api("articles?" + new URLSearchParams(params)),
    enabled: !!data,
    refetchInterval: 10000,
  });
  const article = useQuery({
    queryKey: ["article", selected],
    queryFn: () => api("articles/" + selected),
    enabled: !!selected,
    // Text on its way from the page arrives within seconds of the fetch.
    refetchInterval: (query) =>
      query.state.data?.extracting && !query.state.data.body ? 2000 : 10000,
  });
  // An article opened before its text was fetched goes to the front of the
  // queue: a Reading List import can leave a thousand pages waiting.
  const waiting = article.data?.extracting && !article.data.body;
  useEffect(() => {
    if (waiting) cmd(...extractJob(selected).slice(0, 2)).catch(() => {});
  }, [waiting, selected]);
  // The tab names what is open: the article, the library, or the stream.
  const tabTitle =
    selected && article.data?.title
      ? article.data.title
      : modal
        ? "Manage library"
        : streamTitle(data, view, scope, q);
  useEffect(() => {
    if (data) document.title = "feedelio - " + tabTitle;
  }, [tabTitle, data]);
  const items = articles.data?.items || [],
    a = article.data;
  // An article opened by its address shows within its feed's stream; one
  // resumed after a policy reload stays in the stream it was read from.
  const placed = useRef(!start.selected || !!start.view);
  useEffect(() => {
    if (!placed.current && a && a.id === start.selected) {
      placed.current = true;
      if (a.feed_id === READING_LIST) setView("reading");
      else if (!scope.feed_id && !scope.folder_id)
        setScope({ feed_id: a.feed_id });
    }
  }, [a]);
  // Unsubscribing from the feed on screen returns to all articles.
  useEffect(() => {
    if (
      data &&
      scope.feed_id &&
      !data.feeds.some((f) => f.id === scope.feed_id)
    )
      setScope({});
  }, [data, scope.feed_id]);
  // The hide-read preference can arrive after the first keypress. Reset on the
  // user's toggle, not on its asynchronous response, so it cannot erase history.
  const navigationKey = JSON.stringify([
    { ...params, unread: undefined },
    collapsed,
  ]);
  const visibleItems = items;
  // A write that cannot touch articles says so, and waits only for what it changed.
  const refresh = (keys) =>
    keys
      ? Promise.all(
          keys.map((key) => cache.invalidateQueries({ queryKey: key })),
        )
      : cache.invalidateQueries();
  const notify = (message, error = false) => setToast({ message, error });
  async function act(name, payload, message, options = {}) {
    setBusy((n) => n + 1);
    try {
      const result = await cmd(name, payload);
      if (
        name === "save_settings" &&
        payload.values.invidious !== undefined &&
        payload.values.invidious !== (settings.invidious || "")
      ) {
        setReloadPolicy(true);
      }
      await refresh(options.invalidate);
      if (message) notify(message);
      return result;
    } catch (error) {
      notify(error.message, true);
      throw error;
    } finally {
      setBusy((n) => n - 1);
    }
  }
  const run = (...args) => act(...args).catch(() => {});
  const pref = (key, value) => {
    if (key === "hide_read") navigation.current.key = null;
    return run("save_settings", { values: { [key]: value } });
  };
  function open(item, navigating = false) {
    if (!navigating) {
      navigation.current = {
        key: navigationKey,
        items: visibleItems,
        index: visibleItems.findIndex((x) => x.id === item.id),
      };
    }
    setSelected(item.id);
    setFocused(item.id);
    setLoadImages(false);
    run("change_articles", { ids: [item.id], read: true, opened: true });
  }
  function navigate(delta) {
    // Keep this reading session's displayed order after opened items disappear
    // from unread-only. A different stream/filter/page starts a fresh session.
    if (navigation.current.key !== navigationKey) {
      navigation.current = {
        key: navigationKey,
        items: visibleItems,
        index: visibleItems.findIndex((x) => x.id === selected),
      };
    }
    const trail = navigation.current;
    const index = trail.index < 0 ? 0 : trail.index + delta;
    const next = trail.items[index];
    if (next) {
      trail.index = index;
      open(next, true);
    }
  }
  function stream(nextView, nextScope = {}) {
    setSelected(null);
    setView(nextView);
    setScope(nextScope);
    setOffset(0);
    setFocused(null);
    setQ("");
    setSearchOpen(false);
  }
  function openTitle(item) {
    if (settings.open_titles_in === "original") openExternal(item.url);
    else open(item);
  }
  function markAll() {
    if (
      (articles.data?.unread || 0) > 50 &&
      !confirm(`Mark ${articles.data.unread} articles as read?`)
    )
      return;
    run(
      "mark_all",
      { ...scope, view, q: deferredQ, titles_only: titlesOnly },
      "Stream marked as read · Z to undo",
    );
  }
  useEffect(() => {
    if (reloadPolicy && !modal) {
      try {
        sessionStorage.setItem(
          "feedelio-policy-resume",
          JSON.stringify({ view, scope, q, offset, selected }),
        );
      } catch {
        /* Reload remains safe without storage. */
      }
      location.reload();
    }
  }, [reloadPolicy, modal]);
  useEffect(() => {
    document.documentElement.dataset.theme = settings.theme || "dark";
    document.documentElement.dataset.accent = settings.accent || "blue";
    document.documentElement.dataset.feedColours = String(
      settings.feed_colours !== false,
    );
    document.documentElement.dataset.unreadMarker =
      settings.unread_marker || "count";
    document.documentElement.dataset.reduceMotion = String(
      !!settings.reduce_motion,
    );
  }, [
    settings.theme,
    settings.accent,
    settings.feed_colours,
    settings.unread_marker,
    settings.reduce_motion,
  ]);
  useEffect(() => {
    if (searchOpen) searchRef.current?.focus();
  }, [searchOpen]);
  useEffect(() => {
    setOffset(0);
  }, [deferredQ, settings.hide_read, settings.sort]);
  useEffect(() => {
    if (
      articles.data &&
      !articles.isFetching &&
      offset >= articles.data.total &&
      offset > 0
    ) {
      setOffset(Math.max(0, Math.floor((articles.data.total - 1) / 100) * 100));
    }
  }, [articles.data, articles.isFetching, offset]);
  useEffect(() => {
    if (toast) {
      const timer = setTimeout(() => setToast(null), 5500);
      return () => clearTimeout(timer);
    }
  }, [toast]);
  useEffect(() => {
    readingRef.current?.scrollTo(0, 0);
  }, [selected]);
  useEffect(() => {
    function key(e) {
      if (e.key === "Escape" && searchOpen && !modal) {
        setSearchOpen(false);
        setQ("");
        searchRef.current?.blur();
        return;
      }
      if (e.key === "?" && modal === "keys") {
        setModal(null);
        return;
      }
      const editing = ["INPUT", "TEXTAREA", "SELECT"].includes(
        e.target.tagName,
      );
      // Ctrl+Z (Cmd+Z) undoes the last change, like z, outside text fields.
      if (
        (e.ctrlKey || e.metaKey) &&
        !e.shiftKey &&
        !e.altKey &&
        e.key.toLowerCase() === "z" &&
        !modal &&
        !editing
      ) {
        e.preventDefault();
        run("undo", {}, "Last change undone");
        return;
      }
      if (modal || editing || e.metaKey || e.ctrlKey || e.altKey) return;
      const target = items.find((x) => x.id === focused) || a;
      if (goKey.current && Date.now() - goKey.current < 1500) {
        goKey.current = 0;
        if (e.key === "a" || e.key === "s") {
          e.preventDefault();
          stream(e.key === "a" ? "all" : "starred");
          return;
        }
      }
      if (e.key === "g") {
        goKey.current = Date.now();
        return;
      }
      if (e.key === "j" || e.key === "ArrowDown") {
        e.preventDefault();
        navigate(1);
      } else if (e.key === "k" || e.key === "ArrowUp") {
        e.preventDefault();
        navigate(-1);
      } else if (e.key === "/" || e.key === "f") {
        e.preventDefault();
        setSearchOpen(true);
        searchRef.current?.focus();
      } else if (e.key === "s" && target)
        run("change_articles", { ids: [target.id], starred: !target.starred });
      else if (e.key === "m" && target)
        run("change_articles", { ids: [target.id], read: !target.read });
      else if (e.key === "u") pref("hide_read", !settings.hide_read);
      else if (e.key === "z") run("undo", {}, "Last change undone");
      else if (e.key === "Enter" && target) {
        e.preventDefault();
        if (e.shiftKey) openExternal(target.url);
        else open(target);
      } else if (e.key === "A") {
        e.preventDefault();
        markAll();
      } else if (e.key === ",") setModal("feeds");
      else if (["1", "2", "3"].includes(e.key))
        pref("list_display", { 1: "title", 2: "preview", 3: "compact" }[e.key]);
      else if (e.key === "[" || e.key === "]") {
        const width = Math.max(
          300,
          Math.min(
            1000,
            viewport - railSize - readerMin,
            listSize + (e.key === "[" ? -20 : 20),
          ),
        );
        setListWidth(width);
        pref("list_width", width);
      } else if (e.key === "n" || e.key === "p") {
        // Next and previous stay among the feeds the pane lists.
        const feeds = data.feeds.filter(inPane);
        const index = feeds.findIndex((f) => f.id === scope.feed_id);
        const feed =
          feeds[
            Math.max(
              0,
              Math.min(feeds.length - 1, index + (e.key === "n" ? 1 : -1)),
            )
          ];
        if (feed) stream(paneView, { feed_id: feed.id });
      } else if (e.key === "o" && a) openExternal(a.url);
      else if (e.key === "r") run(...refreshJob(scope.feed_id));
      else if (e.key === "a") setModal("subscribe");
      else if (e.key === "?") setModal("keys");
      else if (e.key === " ") {
        e.preventDefault();
        readingRef.current?.scrollBy({
          top: readingRef.current.clientHeight * 0.85,
          behavior: "instant",
        });
      } else if (e.key === "Escape") {
        setSelected(null);
        setQ("");
        searchRef.current?.blur();
      }
    }
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  });
  if (overview.error?.status === 401)
    return (
      <main className="login">
        <div className="brand-mark">
          <Rss />
        </div>
        <h1>Your little room to read.</h1>
        <p>Enter your Feedelio access token to open your library.</p>
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            try {
              await api("login", { payload: { token } });
              refresh();
            } catch (err) {
              notify(err.message, true);
            }
          }}
        >
          <input
            aria-label="Access token"
            type="password"
            value={token}
            onChange={(e) => setToken(e.target.value)}
            required
          />
          <button className="primary">
            Open Feedelio <ArrowUpRight size={16} />
          </button>
        </form>
        {toast && <p role="alert">{toast.message}</p>}
      </main>
    );
  if (!data)
    return (
      <main className="loading">
        <Rss size={28} />
        <h2>
          {overview.error
            ? "Couldn’t open your library"
            : "Opening your library…"}
        </h2>
        <p>{overview.error?.message}</p>
        {overview.error && (
          <button onClick={() => overview.refetch()}>Try again</button>
        )}
      </main>
    );

  const activeFolder = data.folders.find((f) => f.id === scope.folder_id),
    activeFeed = data.feeds.find((f) => f.id === scope.feed_id);
  const heading = streamTitle(data, view, scope, q);
  const openFeedSettings = (id) => {
    setManageFeed(id);
    setModal("feeds");
  };
  const count = articles.data?.total || 0;
  const archive =
    activeFeed &&
    (data.backfills || []).find((j) => j.payload.feed_id === activeFeed.id);
  // The feeds pane belongs to the streams a feed list can narrow; the tray
  // to its left is always there.
  // Bookmarked feeds narrows like All articles, over bookmarked feeds only.
  const paneView = view === "bookmarks" ? "bookmarks" : "all";
  const inPane = (f) => paneView === "all" || !!f.options.bookmarked;
  const paneShown =
    view === "all" ||
    view === "bookmarks" ||
    !!scope.feed_id ||
    !!scope.folder_id;
  const sidebarSize = Math.max(
    180,
    Math.min(
      paneWidth ?? settings.sidebar_width ?? 252,
      Math.max(180, viewport - TRAY_WIDTH - 680),
    ),
  );
  const railSize = TRAY_WIDTH + (paneShown ? sidebarSize : 0);
  // The reader is what the other two panes are for, so it keeps enough room
  // for a readable measure and the list gives way first.
  const readerMin = Math.round(Math.min(560, Math.max(380, viewport * 0.36)));
  const listSize = Math.max(
    300,
    Math.min(
      listWidth ?? settings.list_width ?? 590,
      Math.max(300, viewport - railSize - readerMin),
    ),
  );
  // Unread-only hides caught-up feeds and folders, but never the one being read.
  const unreadOnly = !!settings.sidebar_unread_only,
    keep = new Set([scope.folder_id]);
  for (
    let id = data.feeds.find((f) => f.id === scope.feed_id)?.folder_id;
    id;
    id = data.folders.find((f) => f.id === id)?.parent_id
  )
    keep.add(id);
  const shown = (f) =>
    inPane(f) && (!unreadOnly || f.unread > 0 || scope.feed_id === f.id);
  // Under Bookmarked feeds the pane lists only bookmarked feeds, the folders
  // holding them, and folder counts from those feeds alone.
  const paneFeeds = data.feeds.filter(inPane);
  const within = (feed, folderId) => {
    for (
      let id = feed.folder_id;
      id;
      id = data.folders.find((f) => f.id === id)?.parent_id
    )
      if (id === folderId) return true;
    return false;
  };
  const folderUnread = (folder) =>
    paneView === "all"
      ? folder.unread
      : paneFeeds
          .filter((f) => within(f, folder.id))
          .reduce((n, f) => n + f.unread, 0);
  const folderTree = (parent = null, depth = 0) =>
    data.folders
      .filter(
        (f) =>
          f.parent_id === parent &&
          (paneView === "all" || paneFeeds.some((x) => within(x, f.id))) &&
          (!unreadOnly || folderUnread(f) > 0 || keep.has(f.id)),
      )
      .map((f) => (
        <React.Fragment key={f.id}>
          <div
            className={
              "folder-line " + (scope.folder_id === f.id ? "active" : "")
            }
            style={{ paddingLeft: 12 + depth * 14 }}
          >
            <button
              className="disclosure"
              aria-label={
                (collapsed["nav" + f.id] ? "Expand " : "Collapse ") + f.name
              }
              onClick={() => toggleFolder(f.id)}
            >
              {collapsed["nav" + f.id] ? (
                <ChevronRight size={14} />
              ) : (
                <ChevronDown size={14} />
              )}
            </button>
            <button onClick={() => stream(paneView, { folder_id: f.id })}>
              <Folder size={16} />
              <span onMouseEnter={fullOnHover}>{f.name}</span>
              <span className="count" data-unread={folderUnread(f)}>
                {folderUnread(f) || ""}
              </span>
            </button>
          </div>
          {!collapsed["nav" + f.id] && (
            <>
              {folderTree(f.id, depth + 1)}
              {data.feeds
                .filter((x) => x.folder_id === f.id && shown(x))
                .map((f) => (
                  <button
                    key={f.id}
                    className={
                      "feed-line " +
                      feedClass(f) +
                      " " +
                      (scope.feed_id === f.id ? "active" : "")
                    }
                    style={{ paddingLeft: 41 + depth * 14 }}
                    onClick={() => stream(paneView, { feed_id: f.id })}
                    {...middleClick(
                      (paneView === "bookmarks" ? "/bookmarks" : "") +
                        "/f/" +
                        f.id,
                    )}
                  >
                    <i className="hue-dot" />
                    <span onMouseEnter={fullOnHover}>{f.title}</span>
                    {f.health === "error" && (
                      <AlertCircle size={14} className="error-color" />
                    )}
                    <span className="count" data-unread={f.unread}>
                      {f.unread || ""}
                    </span>
                  </button>
                ))}
            </>
          )}
        </React.Fragment>
      ));

  // The tray works from the library screen too: it leaves the library first.
  const libraryOpen = LIBRARY_TABS.includes(modal);
  const leave = (fn) => () => {
    if (modal) setModal(null);
    fn();
  };
  const trayButton = ([id, Icon, label, onClick, active, badge]) => (
    <button
      key={id}
      className={active ? "active" : ""}
      aria-label={label}
      aria-current={active ? "page" : undefined}
      title={label}
      onClick={leave(onClick)}
    >
      <Icon size={22} />
      {badge > 0 && (
        <span className="badge" data-unread={badge}>
          {badge > 9999 ? "9999+" : badge}
        </span>
      )}
    </button>
  );
  const onStream = (id) => view === id && !searchOpen && !libraryOpen;
  const tray = (
    <nav className="tray" aria-label="Streams">
      <a className="brand" href="/" aria-label="Feedelio home" title="Feedelio">
        <span
          className={
            settings.wordmark === "plain" ? "plain-wordmark" : "script-f"
          }
        >
          f
        </span>
      </a>
      {[
        [
          "all",
          Inbox,
          "All articles",
          () => stream("all"),
          onStream("all"),
          data.counts.unread,
        ],
        [
          "starred",
          Star,
          "Saved",
          () => stream("starred"),
          onStream("starred"),
        ],
        [
          "bookmarks",
          Bookmark,
          "Bookmarked feeds",
          () => stream("bookmarks"),
          onStream("bookmarks"),
        ],
        [
          "reading",
          BookOpen,
          "Reading list",
          () => stream("reading"),
          onStream("reading"),
          data.counts.reading,
        ],
        [
          "history",
          Clock3,
          "History",
          () => stream("history"),
          onStream("history"),
        ],
      ].map(trayButton)}
      <span className="tray-gap" />
      {[
        [
          "manage",
          Settings2,
          "Manage library",
          () => setModal("feeds"),
          libraryOpen,
        ],
        [
          "search",
          Search,
          "Search",
          () => {
            setSearchOpen(true);
            searchRef.current?.focus();
          },
          searchOpen && !libraryOpen,
        ],
        [
          "subscribe",
          Plus,
          "Add a subscription",
          () => setModal("subscribe"),
          modal === "subscribe",
        ],
      ].map(trayButton)}
    </nav>
  );

  return (
    <main
      className={"shell" + (paneShown ? "" : " no-pane")}
      style={{
        "--sidebar-width": sidebarSize + "px",
        "--list-width": listSize + "px",
      }}
    >
      {tray}
      {paneShown && (
        <aside className="sidebar" aria-label="Feeds">
          <div className="subscriptions-heading">
            <h2>FEEDS</h2>
            <IconButton
              icon={settings.sidebar_unread_only ? EyeOff : Eye}
              label={
                settings.sidebar_unread_only
                  ? "Show all feeds"
                  : "Show only feeds with unread articles"
              }
              aria-pressed={!!settings.sidebar_unread_only}
              onClick={() =>
                pref("sidebar_unread_only", !settings.sidebar_unread_only)
              }
            />
          </div>
          <div className="folder-tree" onScroll={showScrollbar}>
            {paneFeeds.length ? (
              folderTree()
            ) : paneView === "bookmarks" ? (
              <p className="pane-empty">
                No bookmarked feeds yet. Bookmark a feed from the button beside
                its name above the article list.
              </p>
            ) : (
              <button
                className="add-first"
                onClick={() => setModal("subscribe")}
              >
                <Plus size={16} /> Add your first subscription
              </button>
            )}
          </div>
        </aside>
      )}
      <section className="stream-pane" aria-label="Article stream">
        {paneShown && (
          <ResizeHandle
            label="Resize feeds pane"
            value={sidebarSize}
            min={180}
            max={Math.min(600, viewport - TRAY_WIDTH - 300 - readerMin)}
            start={TRAY_WIDTH}
            onChange={setPaneWidth}
            onSave={(v) => {
              setPaneWidth(v);
              pref("sidebar_width", v);
            }}
          />
        )}
        <div className="list-heading">
          {searchOpen ? (
            <div className="search-prompt">
              <span>/</span>
              <input
                ref={searchRef}
                aria-label="Search articles"
                placeholder="Search articles"
                value={q}
                onChange={(e) => setQ(e.target.value)}
              />
              <button
                onClick={() => {
                  setSearchOpen(false);
                  setQ("");
                }}
              >
                esc to close
              </button>
            </div>
          ) : activeFeed ? (
            <HeadingLink
              title="Feed settings"
              label={"Manage feed: " + activeFeed.title}
              onClick={() => openFeedSettings(activeFeed.id)}
            >
              {heading}
            </HeadingLink>
          ) : scope.folder_id && !searchOpen ? (
            <HeadingLink
              title="Folder settings"
              label={"Manage folder: " + heading}
              onClick={() => {
                setManageFolder(scope.folder_id);
                setModal("folders");
              }}
            >
              {heading}
            </HeadingLink>
          ) : (
            <h1>{heading}</h1>
          )}
          <div className="stream-meta">
            <span>
              {count.toLocaleString()}{" "}
              {searchOpen ? "results" : count === 1 ? "article" : "articles"} ·{" "}
              {articles.data?.unread || 0} unread
            </span>
            <div className="stream-controls">
              {view !== "reading" && (
                <UpdateNow
                  data={data}
                  act={run}
                  feedId={scope.feed_id}
                  checked={activeFeed?.checked}
                />
              )}
              <Switch
                label="Unread only"
                checked={!!settings.hide_read}
                onChange={(v) => pref("hide_read", v)}
              />
              <button
                className="sort-toggle"
                onClick={() =>
                  pref("sort", settings.sort === "oldest" ? "newest" : "oldest")
                }
              >
                <ArrowUpDown size={16} />
                {settings.sort === "oldest" ? "Oldest first" : "Newest first"}
              </button>
              {activeFeed && (
                <IconButton
                  icon={Bookmark}
                  className={
                    "icon-button " +
                    (activeFeed.options.bookmarked ? "bookmarked" : "")
                  }
                  label={
                    activeFeed.options.bookmarked
                      ? "Remove bookmark"
                      : "Bookmark this feed"
                  }
                  aria-pressed={!!activeFeed.options.bookmarked}
                  onClick={() =>
                    run(
                      "edit_feed",
                      {
                        id: activeFeed.id,
                        options: {
                          ...activeFeed.options,
                          bookmarked: !activeFeed.options.bookmarked,
                        },
                      },
                      activeFeed.options.bookmarked
                        ? "Bookmark removed"
                        : "Feed bookmarked",
                    )
                  }
                />
              )}
              <IconButton
                icon={CheckCheck}
                label="Mark this stream as read"
                onClick={markAll}
              />
            </div>
          </div>
          {archive && !searchOpen && (
            <button
              className="archive-progress"
              aria-label="Archive progress"
              onClick={() => openFeedSettings(activeFeed.id)}
            >
              {archive.status === "running" && (
                <Loader2 size={14} className="spin" />
              )}
              {archiveSummary(archive)}
            </button>
          )}
          {searchOpen && (
            <div className="search-filters">
              <button
                aria-pressed={titlesOnly}
                className={titlesOnly ? "active" : ""}
                onClick={() => setTitlesOnly(!titlesOnly)}
              >
                Titles only
              </button>
              <span>{data.feeds.length} feeds · all time</span>
            </div>
          )}
        </div>
        <div
          className={
            "article-list " +
            (searchOpen ? "search-results" : settings.list_display || "title") +
            (scope.feed_id ? " single-feed" : "")
          }
          aria-label="Article list"
          tabIndex={0}
        >
          {articles.error && (
            <div className="inline-error" role="alert">
              {articles.error.message}
              <button onClick={() => articles.refetch()}>Try again</button>
            </div>
          )}
          {articles.isPending ? (
            <div className="list-empty">
              <Loader2 className="spin" size={22} />
              <p>Gathering your articles…</p>
            </div>
          ) : !items.length ? (
            <div className="list-empty">
              <Leaf size={28} />
              <h3>
                {q
                  ? "No matches yet"
                  : view === "reading"
                    ? "Nothing on your reading list."
                    : data.feeds.length
                      ? "A quiet moment."
                      : "A fresh start."}
              </h3>
              <p>
                {q
                  ? "Try a different word or turn off unread only."
                  : view === "reading"
                    ? "Pages you add to Chrome’s Reading List show here, through the Feedelio companion extension (Manage library → Import & export)."
                    : data.feeds.length
                      ? "You’re all caught up here. New articles will arrive automatically."
                      : "The things you love to read will find a home here."}
              </p>
              {!data.feeds.length && view !== "reading" && (
                <button onClick={() => setModal("subscribe")}>
                  Add a subscription <ArrowUpRight size={14} />
                </button>
              )}
            </div>
          ) : (
            items.map((item) => (
              <div
                key={item.id}
                className={
                  "article-row " +
                  feedClass(data.feeds.find((f) => f.id === item.feed_id)) +
                  " " +
                  (selected === item.id ? "selected " : "") +
                  (focused === item.id ? "focused " : "") +
                  (item.read ? "read" : "unread")
                }
                aria-current={selected === item.id ? "true" : undefined}
                onClick={() => setFocused(item.id)}
              >
                <div className="row-state">
                  <ReadStar
                    item={item}
                    run={run}
                    labels={{
                      read: "Mark read: " + item.title,
                      unread: "Mark unread: " + item.title,
                      star: "Star: " + item.title,
                      unstar: "Unstar: " + item.title,
                    }}
                  />
                </div>
                {item.feed_id === READING_LIST ? (
                  <span
                    className="row-feed"
                    onMouseEnter={fullOnHover}
                    {...middleClick(item.url && new URL(item.url).origin)}
                  >
                    {item.feed_title}
                  </span>
                ) : (
                  !scope.feed_id && (
                    <button
                      className="row-feed"
                      onMouseEnter={fullOnHover}
                      onClick={() => stream("all", { feed_id: item.feed_id })}
                      {...middleClick("/f/" + item.feed_id)}
                    >
                      {item.feed_title}
                    </button>
                  )
                )}
                <button
                  className="row-title"
                  onMouseEnter={fullOnHover}
                  onClick={() => openTitle(item)}
                  // Middle click opens the original in a new tab, like a link.
                  {...middleClick(item.url)}
                >
                  <ArticleMarks item={item} />
                  <Highlight text={item.title} query={q} />
                </button>
                <time
                  className="row-date"
                  dateTime={item.published}
                  title={fullDate(item.published)}
                >
                  {ago(item.published)}
                </time>
                {(settings.list_display === "preview" || searchOpen) && (
                  <p className="row-snippet">
                    <Highlight text={item.excerpt} query={q} />
                  </p>
                )}
              </div>
            ))
          )}
          {(count > 100 || offset > 0) && (
            <div className="pagination">
              <button
                disabled={!offset}
                onClick={() => setOffset(Math.max(0, offset - 100))}
              >
                <ChevronLeft size={16} />
                Previous
              </button>
              <span>
                {offset + 1}–{Math.min(offset + 100, count)}
              </span>
              <button
                disabled={offset + 100 >= count}
                onClick={() => setOffset(offset + 100)}
              >
                Next
                <ChevronRight size={16} />
              </button>
            </div>
          )}
        </div>
      </section>
      <section className="reader-pane" aria-label="Reading pane">
        <ResizeHandle
          label="Resize article list"
          value={listSize}
          min={300}
          max={Math.min(1000, viewport - railSize - readerMin)}
          start={railSize}
          onChange={setListWidth}
          onSave={(v) => {
            setListWidth(v);
            pref("list_width", v);
          }}
        />
        {!selected ? (
          <div className="reader-empty">
            <BookOpen size={28} />
            <h2>A little room to read.</h2>
            <p>Choose an article title to start reading.</p>
            <button onClick={() => setModal("keys")}>
              <kbd>?</kbd> Keyboard shortcuts
            </button>
          </div>
        ) : article.isPending ? (
          <div className="loading">
            <Loader2 className="spin" />
            <p>Opening article…</p>
          </div>
        ) : article.error ? (
          <div className="loading">
            <AlertCircle />
            <p>{article.error.message}</p>
            <button onClick={() => article.refetch()}>Try again</button>
          </div>
        ) : (
          a && (
            <>
              <div ref={readingRef} className="reading-scroll">
                <article
                  className={
                    "reading-content font-" + (settings.font || "serif")
                  }
                  style={{
                    "--reading-size": (settings.font_size || 17) + "px",
                    "--reading-width": (settings.width || 65) + "ch",
                    "--reading-line": settings.line_height || 1.7,
                  }}
                >
                  <h1>
                    <a href={a.url} target="_blank" rel="noreferrer">
                      {a.title}
                    </a>
                  </h1>
                  <div
                    className={
                      "article-kicker " +
                      feedClass(data.feeds.find((f) => f.id === a.feed_id))
                    }
                  >
                    <i className="hue-dot" />
                    {a.feed_id === READING_LIST ? (
                      <span {...middleClick(a.url && new URL(a.url).origin)}>
                        {a.feed_title}
                      </span>
                    ) : (
                      <button
                        onClick={() => stream("all", { feed_id: a.feed_id })}
                        {...middleClick("/f/" + a.feed_id)}
                      >
                        {a.feed_title}
                      </button>
                    )}
                    <span className="kicker-sep">·</span>
                    <time dateTime={a.published}>{fullDate(a.published)}</time>
                  </div>
                  <div className="reader-meta">
                    <details className="article-details">
                      <summary>
                        {a.duration
                          ? `${clock(a.duration)} ${
                              (a.enclosures || []).some((e) =>
                                (e.type || "").startsWith("audio/"),
                              )
                                ? "listen"
                                : "video"
                            }`
                          : `${a.reading_minutes} min read · ${a.words.toLocaleString()} words`}
                      </summary>
                      {a.author && <p>By {a.author}</p>}
                    </details>
                    <span className="reader-meta-actions">
                      <ReadStar
                        item={a}
                        run={run}
                        labels={{
                          read: "Mark article read",
                          unread: "Mark article unread",
                          star: "Save for later",
                          unstar: "Unsave article",
                        }}
                      />
                      <IconButton
                        icon={Filter}
                        label="Add a filter like this article"
                        onClick={() => setModal("filter")}
                      />
                    </span>
                  </div>
                  <hr className="reader-rule" />
                  {a.embed && (
                    <div className="video-box">
                      <iframe
                        title={a.title}
                        src={a.embed}
                        allow="fullscreen; picture-in-picture"
                        allowFullScreen
                        referrerPolicy="strict-origin-when-cross-origin"
                      />
                    </div>
                  )}
                  {a.enclosures
                    .filter((e) => (e.type || "").startsWith("audio/"))
                    .map((e) => {
                      const saved = a.downloads.find(
                        (d) => d.status === "done" && d.url === e.href,
                      );
                      return (
                        <div className="podcast" key={e.href}>
                          <div>
                            <Headphones size={20} />
                            <strong>Listen to this episode</strong>
                            <button
                              onClick={() =>
                                run(
                                  "enqueue",
                                  {
                                    kind: "download",
                                    payload: { article_id: a.id, url: e.href },
                                  },
                                  "Episode added to download queue",
                                )
                              }
                            >
                              <Download size={16} />
                              Download
                            </button>
                          </div>
                          <audio
                            controls
                            preload="none"
                            src={saved ? "/api/downloads/" + saved.id : e.href}
                          />
                        </div>
                      );
                    })}
                  {!settings.images &&
                    !loadImages &&
                    a.body.includes("<img") && (
                      <button
                        className="image-notice"
                        onClick={() => setLoadImages(true)}
                      >
                        <Image size={16} />
                        Images are paused. Load images for this article.
                      </button>
                    )}
                  {a.body ? (
                    <SafeArticle
                      body={a.body}
                      images={settings.images || loadImages}
                    />
                  ) : a.extracting ? (
                    <div className="no-body">
                      <Loader2 className="spin" size={28} />
                      <p>Fetching the article text from its page…</p>
                    </div>
                  ) : (
                    <div className="no-body">
                      <FileText size={28} />
                      <p>This feed provided a link without article text.</p>
                      <button
                        className="primary"
                        onClick={() => run(...extractJob(a.id))}
                      >
                        Fetch full article <ArrowUpRight size={16} />
                      </button>
                    </div>
                  )}
                  {a.transcript && (
                    <section className="transcript">
                      <h2>Transcript</h2>
                      <p>{a.transcript}</p>
                    </section>
                  )}
                  <div className="article-end">
                    <span />
                    <Leaf size={18} />
                    <span />
                  </div>
                  <div className="article-footer">
                    <span>You’ve reached the end.</span>
                    <button onClick={() => navigate(1)}>
                      Next article <ChevronRight size={16} />
                    </button>
                  </div>
                  <div className="secondary-tools">
                    <button onClick={() => run(...extractJob(a.id))}>
                      <FileText size={14} />
                      Fetch full text
                    </button>
                    <a href={"/api/articles/" + a.id + "/obsidian"} download>
                      <Download size={14} />
                      Obsidian / Markdown
                    </a>
                    <button onClick={() => setModal("tags")}>
                      <Plus size={14} />
                      Tags
                    </button>
                    <button
                      onClick={() => {
                        run(
                          "change_articles",
                          { ids: [a.id], deleted: true },
                          "Article deleted · Z to undo",
                        );
                        setSelected(null);
                      }}
                    >
                      <Trash2 size={14} />
                      Delete
                    </button>
                  </div>
                  {a.tags.length > 0 && (
                    <div className="article-tags">
                      {a.tags.map((t) => (
                        <button
                          className="tag"
                          key={t}
                          onClick={() => {
                            stream("all", { tag: t });
                          }}
                        >
                          {t}
                        </button>
                      ))}
                    </div>
                  )}
                </article>
              </div>
              <footer className="reading-footer">
                <span>
                  <kbd>j/k</kbd> move <kbd>m</kbd> mark read <kbd>s</kbd> star{" "}
                  <kbd>↵</kbd> open
                </span>
              </footer>
            </>
          )
        )}
      </section>
      {toast && (
        <div
          className={"toast " + (toast.error ? "error" : "")}
          role={toast.error ? "alert" : "status"}
        >
          {toast.error ? <AlertCircle size={16} /> : <Check size={16} />}
          <span>{toast.message}</span>
          {!toast.error && data.undo.length > 0 && (
            <button onClick={() => run("undo", {}, "Last change undone")}>
              <Undo2 size={14} />
              Undo
            </button>
          )}
          <IconButton
            icon={X}
            label="Dismiss notification"
            onClick={() => setToast(null)}
          />
        </div>
      )}
      {modal === "subscribe" && (
        <Subscribe data={data} act={act} onClose={() => setModal(null)} />
      )}
      {modal === "filter" && a && (
        <RuleSheet
          data={data}
          act={act}
          api={api}
          draft={{
            name: a.title,
            field: "title",
            pattern: a.title,
            is_regex: false,
            action: "read",
            feed_id: a.feed_id,
          }}
          onClose={() => setModal(null)}
          Modal={Modal}
          Field={Field}
        />
      )}
      {modal === "keys" && (
        <Modal
          title="Keyboard"
          subtitle="? to toggle · esc to close"
          onClose={() => setModal(null)}
          wide
        >
          <KeyboardGuide />
        </Modal>
      )}
      {modal === "tags" && (
        <Modal title="Article tags" onClose={() => setModal(null)}>
          <form
            className="dialog-body"
            onSubmit={async (e) => {
              e.preventDefault();
              await act(
                "change_articles",
                {
                  ids: [a.id],
                  tags: parseTags(new FormData(e.currentTarget).get("tags")),
                },
                "Tags saved",
              );
              setModal(null);
            }}
          >
            <Field label="Tags, separated by commas">
              <input name="tags" defaultValue={a.tags.join(", ")} />
            </Field>
            <button className="primary">Save tags</button>
          </form>
        </Modal>
      )}
      {LIBRARY_TABS.includes(modal) && (
        <Library
          initial={modal}
          initialFeed={manageFeed}
          initialFolder={manageFolder}
          layers={layers}
          pref={pref}
          data={data}
          act={act}
          onClose={() => {
            setModal(null);
            setManageFeed(null);
            setManageFolder(null);
          }}
          onSubscribe={() => setModal("subscribe")}
          tray={tray}
        />
      )}
    </main>
  );
}

function Favicon({ feed, enabled }) {
  const [failed, setFailed] = useState(false);
  return enabled && feed.favicon && !failed ? (
    <img
      className="favicon"
      src={feed.favicon}
      loading="lazy"
      referrerPolicy="no-referrer"
      alt=""
      onError={() => setFailed(true)}
    />
  ) : (
    <span className="favicon-letter">{feed.title[0]?.toUpperCase()}</span>
  );
}
function SafeArticle({ body, images }) {
  const sanitized = DOMPurify.sanitize(body, {
    FORBID_TAGS: [
      "iframe",
      "form",
      "input",
      "style",
      "script",
      "object",
      "embed",
      "svg",
      "video",
      "audio",
      ...(!images ? ["img"] : []),
    ],
    FORBID_ATTR: ["style", "srcset"],
  });
  return (
    <div className="prose" dangerouslySetInnerHTML={{ __html: sanitized }} />
  );
}

function Subscribe({ data, act, onClose }) {
  const [chosen, setChosen] = useState("");
  const [url, setUrl] = useState(
      new URLSearchParams(location.search).get("subscribe") || "",
    ),
    [links, setLinks] = useState([]),
    { error, busy: loading, submit } = useSubmit(),
    [advanced, setAdvanced] = useState(false);
  const [folder, setFolder] = useState("inbox"),
    [title, setTitle] = useState(""),
    [selector, setSelector] = useState(""),
    [bridge, setBridge] = useState("");
  async function find(e) {
    e.preventDefault();
    const result = await submit(() => cmd("discover", { url }));
    if (!Array.isArray(result)) return;
    setLinks(result);
    setChosen(result[0]?.url || "");
  }
  async function add(feedUrl) {
    const done = await submit(() =>
      act(
        "subscribe",
        {
          url: feedUrl,
          folder_id: folder,
          title,
          options: selector ? { scrape_selector: selector, extract: true } : {},
        },
        "Subscription added. The worker will fetch it shortly.",
      ),
    );
    if (done) onClose();
  }
  return (
    <Modal
      title="Add feed"
      subtitle="Site URL, feed URL, or YouTube channel URL"
      onClose={onClose}
    >
      <form
        className="dialog-body"
        onSubmit={(e) => {
          if (links.length && chosen) {
            e.preventDefault();
            add(chosen);
          } else find(e);
        }}
      >
        <Field label="Website or feed URL">
          <input
            autoFocus
            type="url"
            placeholder="https://example.com"
            value={url}
            onChange={(e) => {
              setUrl(e.target.value);
              setLinks([]);
              setChosen("");
            }}
            required
          />
        </Field>
        <div className="form-grid">
          <Field label="Folder">
            <FolderSelect
              data={data}
              act={act}
              label="Folder"
              value={folder}
              onChange={setFolder}
            />
          </Field>
          <Field label="Custom title (optional)">
            <input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="A familiar name"
            />
          </Field>
        </div>
        {error && (
          <p className="inline-error" role="alert">
            {error}
          </p>
        )}
        {links.length > 0 && (
          <div className="discovered">
            <h3 className="section-label">
              {links.length === 1
                ? "One feed found"
                : `${links.length} feeds found`}
            </h3>
            {links.map((link) => (
              <button
                type="button"
                key={link.url}
                disabled={loading}
                className={chosen === link.url ? "selected" : ""}
                aria-pressed={chosen === link.url}
                onClick={() => setChosen(link.url)}
              >
                <i className="hue-dot" />
                <span>
                  <strong>{link.title}</strong>
                  <small>{link.url}</small>
                </span>
                {chosen === link.url && <Check size={18} />}
              </button>
            ))}
          </div>
        )}
        <div className="form-actions">
          <button
            type="button"
            className="text-button"
            onClick={() => setAdvanced(!advanced)}
          >
            <SlidersHorizontal size={14} />
            Advanced
          </button>
          <button type="button" onClick={onClose}>
            Cancel
          </button>
          <button className="primary" disabled={loading || !url}>
            {loading ? (
              <Loader2 size={16} className="spin" />
            ) : (
              <Search size={16} />
            )}
            {links.length ? "Subscribe" : "Find feeds"}
          </button>
        </div>
        {advanced && (
          <div className="advanced-fields">
            <Field
              label="No feed? Scrape repeating page elements"
              hint="CSS selector for each story card, e.g. article. Each card needs an article link."
            >
              <input
                value={selector}
                onChange={(e) => setSelector(e.target.value)}
                placeholder="article.story"
              />
            </Field>
            <button
              type="button"
              disabled={!selector || !url || loading}
              onClick={() => add(url)}
            >
              Subscribe with site scraper
            </button>
            <Field
              label="RSSHub route"
              hint="Uses your configured RSSHub instance. Newsletter bridges can be added with their feed URL above."
            >
              <input
                value={bridge}
                onChange={(e) => setBridge(e.target.value)}
                placeholder="/github/issue/owner/repo"
              />
            </Field>
            <button
              type="button"
              disabled={!bridge || loading}
              onClick={() =>
                add(
                  data.settings.rsshub.replace(/\/$/, "") +
                    "/" +
                    bridge.replace(/^\//, ""),
                )
              }
            >
              Subscribe through RSSHub
            </button>
            <button
              type="button"
              disabled={!url || loading}
              onClick={() => add(url)}
            >
              Subscribe to this exact feed URL
            </button>
          </div>
        )}
        <p className="privacy-note">
          <ShieldCheck size={14} />
          Your subscriptions stay in your own library.
        </p>
      </form>
    </Modal>
  );
}

function Library({
  initial,
  initialFeed,
  initialFolder,
  layers,
  data,
  act,
  pref,
  onClose,
  onSubscribe,
  tray,
}) {
  const [tab, setTab] = useState(initial),
    [ids, setIds] = useState([]),
    [edit, setEdit] = useState(
      () => data.feeds.find((f) => f.id === initialFeed) || null,
    ),
    [status, setStatus] = useState("all"),
    [fromList, setFromList] = useState(false),
    { error, setError, submit } = useSubmit();
  const [folderSheet, setFolderSheet] = useState(null);
  // A feed opened from the list is a layer, so Back returns to the list; one
  // opened from the stream heading is not, so Back leaves the library.
  useLayer(layers, "feed", !!edit && fromList, () => setEdit(null));
  const openFeed = (f) => {
    setFromList(true);
    setEdit(f);
  };
  const settingsSections = {
    feeds: "Feeds",
    folders: "Folders",
    appearance: "Appearance",
    keyboard: "Keyboard",
    data: "Import & export",
    rules: "Rules",
    downloads: "Downloads",
    statistics: "Activity",
  };
  const downloads = data.downloads || [];
  const lastRefreshed = data.feeds
    .map((f) => f.checked)
    .filter(Boolean)
    .sort()
    .at(-1);
  const libraryRef = useRef(null);
  useEffect(() => {
    libraryRef.current.showModal();
    // The folder sheet is a dialog too: it opens after this one, to sit on top.
    setFolderSheet(data.folders.find((f) => f.id === initialFolder) || null);
  }, []);
  // The library's address names its section, and the feed or folder being edited.
  useEffect(() => {
    const item =
      tab === "feeds" ? edit : tab === "folders" ? folderSheet : null;
    const url = "/library/" + tab + (item?.id ? "/" + item.id : "");
    layers.current.url = url;
    if (location.pathname !== url) history.replaceState(history.state, "", url);
  }, [tab, edit, folderSheet]);
  const [settingsWidth, setSettingsWidth] = useState(
    data.settings.sidebar_width,
  );
  const rules = useQuery({ queryKey: ["rules"], queryFn: () => api("rules") });
  const stats = useQuery({
    queryKey: ["statistics"],
    queryFn: () => api("statistics"),
    enabled: tab === "statistics",
  });
  return (
    <dialog
      className="library-screen"
      aria-label="Manage library"
      ref={libraryRef}
      tabIndex={-1}
      style={{ "--settings-width": settingsWidth + "px" }}
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
    >
      {tray}
      <aside className="settings-sidebar">
        <h2 className="section-label">Manage library</h2>
        <nav className="dialog-tabs" aria-label="Library settings">
          {Object.entries(settingsSections)
            .sort((a, b) => a[1].localeCompare(b[1]))
            .map(([id, label]) => (
              <button
                key={id}
                className={tab === id ? "active" : ""}
                onClick={() => {
                  setTab(id);
                  setEdit(null);
                  setError("");
                }}
              >
                {label}
              </button>
            ))}
        </nav>
      </aside>
      <div className="settings-content">
        <ResizeHandle
          label="Resize settings sidebar"
          value={settingsWidth}
          min={180}
          max={Math.min(600, window.innerWidth - 650)}
          onChange={setSettingsWidth}
          onSave={(v) => {
            setSettingsWidth(v);
            pref("sidebar_width", v);
          }}
        />
        <div className="manager-body">
          <header className="manager-title">
            <h1>{settingsSections[tab]}</h1>
            <IconButton icon={X} label="Close library" onClick={onClose} />
          </header>
          {tab === "appearance" && (
            <>
              <p className="settings-subtitle">
                One ground, one accent — everything else is type.
              </p>
              <AppearanceSettings settings={data.settings} pref={pref} />
            </>
          )}
          {tab === "keyboard" && <KeyboardGuide />}

          {error && (
            <p role="alert" className="inline-error">
              {error}
            </p>
          )}
          {tab === "feeds" && (
            <>
              {edit ? (
                <FeedEditor
                  feed={edit}
                  data={data}
                  act={act}
                  onBack={() => setEdit(null)}
                />
              ) : (
                <>
                  <div className="manager-heading">
                    <span>
                      {data.feeds.length} feeds · {data.folders.length - 1}{" "}
                      folders ·{" "}
                      {!lastRefreshed
                        ? "not refreshed yet"
                        : ago(lastRefreshed) === "now"
                          ? "refreshed just now"
                          : `refreshed ${ago(lastRefreshed)} ago`}
                    </span>
                    <Select
                      label="Filter feeds by status"
                      value={status}
                      options={Object.entries(feedStatuses).map(
                        ([id, label]) => ({
                          value: id,
                          label:
                            label +
                            (id === "all"
                              ? ""
                              : ` (${data.feeds.filter((f) => f.health === id).length})`),
                        }),
                      )}
                      onChange={setStatus}
                    />
                    <button className="primary" onClick={onSubscribe}>
                      <Plus size={16} />
                      Add feed
                    </button>
                  </div>
                  {ids.length > 0 && (
                    <div className="bulk-bar">
                      <strong>{ids.length} selected</strong>
                      <Select
                        label="Move selected feeds"
                        value=""
                        placeholder="Move to folder…"
                        options={folderOptions(data.folders)}
                        onChange={(folder_id) =>
                          submit(() =>
                            act(
                              "bulk_feeds",
                              { ids, action: "move", folder_id },
                              "Feeds moved",
                            ),
                          )
                        }
                      />
                      <button
                        onClick={() =>
                          submit(() =>
                            act(
                              "bulk_feeds",
                              { ids, action: "refresh" },
                              "Update queued",
                            ),
                          )
                        }
                      >
                        <RefreshCw size={16} />
                      </button>
                      <button
                        onClick={() =>
                          submit(async () => {
                            await act(
                              "bulk_feeds",
                              { ids, action: "delete" },
                              "Subscriptions deleted · Z to undo",
                            );
                            setIds([]);
                          })
                        }
                      >
                        <Trash2 size={16} />
                        Delete
                      </button>
                      <form
                        onSubmit={(e) => {
                          e.preventDefault();
                          submit(() =>
                            act(
                              "bulk_feeds",
                              {
                                ids,
                                action: "tag",
                                tags: parseTags(
                                  new FormData(e.currentTarget).get("tags"),
                                ),
                              },
                              "Feed tags updated",
                            ),
                          );
                        }}
                      >
                        <input
                          name="tags"
                          aria-label="Batch feed tags"
                          placeholder="tags, comma separated"
                        />
                        <button>Set tags</button>
                      </form>
                    </div>
                  )}
                  <div className="feed-table">
                    <div className="table-head">
                      <span>Feed</span>
                      <span>Folder</span>
                      <span className="feed-unread">Unread</span>
                      <span className="feed-updated">Updated</span>
                      <span />
                      <span />
                    </div>
                    {data.feeds
                      .filter((f) => status === "all" || f.health === status)
                      .map((f) => {
                        const issue = feedIssue(f);
                        return (
                          <div key={f.id} className={feedClass(f)}>
                            <div className="feed-cell">
                              <input
                                type="checkbox"
                                aria-label={"Select " + f.title}
                                checked={ids.includes(f.id)}
                                onChange={(e) =>
                                  setIds(
                                    e.target.checked
                                      ? [...ids, f.id]
                                      : ids.filter((id) => id !== f.id),
                                  )
                                }
                              />
                              <div className="feed-info">
                                <i className="hue-dot" />
                                <span>
                                  <button
                                    className="feed-open"
                                    onClick={() => openFeed(f)}
                                  >
                                    <strong>{f.title}</strong>
                                  </button>
                                  <small>
                                    <a
                                      href={siteOf(f)}
                                      target="_blank"
                                      rel="noreferrer"
                                    >
                                      {siteOf(f)}
                                    </a>
                                  </small>
                                </span>
                              </div>
                            </div>
                            <span className="feed-folder">
                              {f.folder_id === "inbox"
                                ? "—"
                                : data.folders.find((x) => x.id === f.folder_id)
                                    ?.name}
                            </span>
                            <span className="feed-unread">{f.unread}</span>
                            <span
                              className={
                                "feed-updated " +
                                (issue?.kind || (f.checked ? "healthy" : ""))
                              }
                              title={issue?.note}
                            >
                              {issue?.label || elapsed(f.checked)}
                            </span>
                            <span>
                              {issue?.kind === "error" && (
                                <span
                                  className={"feed-issue " + issue.kind}
                                  title={issue.note}
                                  aria-label={issue.note}
                                >
                                  !
                                </span>
                              )}
                            </span>
                            <IconButton
                              icon={MoreHorizontal}
                              label={"Edit " + f.title}
                              onClick={() => openFeed(f)}
                            />
                          </div>
                        );
                      })}
                  </div>
                  {!data.feeds.length && (
                    <div className="list-empty">
                      <Rss />
                      <h3>Your next good read starts here.</h3>
                      <p>
                        Add a feed or import your Inoreader OPML from Data &
                        connections.
                      </p>
                    </div>
                  )}
                </>
              )}
            </>
          )}
          {tab === "folders" && (
            <FolderManager
              data={data}
              act={act}
              onNew={() => setFolderSheet({})}
              onEdit={setFolderSheet}
            />
          )}
          {tab === "rules" && (
            <RulesManager
              data={data}
              rules={rules}
              act={act}
              api={api}
              Modal={Modal}
              Field={Field}
              IconButton={IconButton}
              Pencil={Pencil}
              Trash2={Trash2}
            />
          )}
          {tab === "data" && (
            <DataSettings data={data} act={act} submit={submit} />
          )}
          {tab === "downloads" && (
            <>
              <p className="muted">
                Downloaded episodes stay on this server and play without
                contacting the publisher. Open an episode to add it to the
                queue.
              </p>
              {downloads.map((j) => (
                <div className="download-row" key={j.id}>
                  <Headphones size={20} />
                  <span>
                    <strong>
                      {j.payload.url.split("/").pop()?.slice(0, 65)}
                    </strong>
                    <small>
                      {j.status}
                      {j.error ? " · " + j.error : ""}
                    </small>
                  </span>
                  {j.status === "done" && (
                    <>
                      <a href={"/api/downloads/" + j.id} download>
                        <Download size={16} />
                      </a>
                      <IconButton
                        icon={Trash2}
                        label="Delete downloaded episode"
                        onClick={() =>
                          submit(() =>
                            act(
                              "delete_download",
                              { id: j.id },
                              "Downloaded file removed; source episode remains available",
                            ),
                          )
                        }
                      />
                    </>
                  )}
                  <RetryJob job={j} act={act} submit={submit} />
                </div>
              ))}
              {!downloads.length && (
                <div className="list-empty">
                  <Headphones size={28} />
                  <h3>A listening queue, at your pace.</h3>
                  <p>Downloaded podcast episodes will appear here.</p>
                </div>
              )}
              <JobsLog data={data} act={act} submit={submit} />
            </>
          )}
          {tab === "statistics" && (
            <>
              {stats.error && <p role="alert">{stats.error.message}</p>}
              {stats.data ? (
                <>
                  <div className="stat-cards">
                    <div>
                      <strong>{stats.data.totals.read}</strong>
                      <span>Articles read</span>
                    </div>
                    <div>
                      <strong>
                        {Math.round(stats.data.totals.words / 230)}
                      </strong>
                      <span>Estimated reading minutes</span>
                    </div>
                    <div>
                      <strong>{stats.data.totals.saved}</strong>
                      <span>Saved for later</span>
                    </div>
                    <div>
                      <strong>{fileSize(stats.data.archive.bytes)}</strong>
                      <span>
                        Archive size · {stats.data.totals.articles} articles
                        {stats.data.archive.downloads
                          ? `, ${stats.data.archive.downloads} downloads`
                          : ""}
                      </span>
                    </div>
                  </div>
                  <h3>Where your attention goes</h3>
                  <div className="habit-table">
                    {stats.data.feeds.map((f) => (
                      <div key={f.title}>
                        <span>{f.title}</span>
                        <meter
                          min="0"
                          max={Math.max(1, f.total)}
                          value={f.read}
                        />
                        <span>
                          {f.read} / {f.total} read
                        </span>
                        <span>{f.saved} saved</span>
                      </div>
                    ))}
                  </div>
                  <h3>Recently opened</h3>
                  {stats.data.days.map((d) => (
                    <div className="activity-day" key={d.day}>
                      <span>{fullDate(d.day)}</span>
                      <span>{d.opens} articles opened</span>
                    </div>
                  ))}
                  <p className="muted">
                    Reading time is estimated at 230 words per minute. Opening
                    an article is counted separately from marking a stream as
                    read.
                  </p>
                </>
              ) : (
                <p>Loading activity…</p>
              )}
            </>
          )}
        </div>
      </div>
      {folderSheet && (
        <FolderSheet
          data={data}
          act={act}
          folder={folderSheet.id ? folderSheet : null}
          onClose={() => setFolderSheet(null)}
          Modal={Modal}
          Field={Field}
        />
      )}
    </dialog>
  );
}

// Feeds that never declared a site link still belong to one: their origin.
function siteOf(f) {
  if (f.site_url) return f.site_url;
  try {
    return new URL(f.url).origin + "/";
  } catch {
    return f.url;
  }
}

function FeedEditor({ feed: f, data, act, onBack }) {
  const { error, setError, submit } = useSubmit(),
    [folderId, setFolderId] = useState(f.folder_id);
  async function save(e) {
    e.preventDefault();
    const p = Object.fromEntries(new FormData(e.currentTarget));
    const done = await submit(() =>
      act(
        "edit_feed",
        {
          id: f.id,
          title: p.title,
          folder_id: p.folder_id,
          tags: parseTags(p.tags),
          options: {
            ...f.options,
            interval: p.interval ? Number(p.interval) : null,
            archive: p.archive === "on",
            bookmarked: p.bookmarked === "on",
            extract: p.extract === "on",
            mark_read: p.mark_read === "on",
            selector: p.selector,
            user_agent: p.user_agent,
            cookie: p.cookie,
            proxy: p.proxy,
            backfill_url: p.backfill_url,
            scrape_selector: p.scrape_selector,
            transcript_selector: p.transcript_selector,
          },
        },
        "Feed settings saved",
      ),
    );
    if (done) onBack();
  }
  return (
    <form onSubmit={save}>
      <button type="button" className="text-button" onClick={onBack}>
        <ChevronLeft size={16} />
        Subscriptions
      </button>
      <h3 className="feed-editor-title">
        <Favicon feed={f} enabled={data.settings.images} />
        {f.title}
      </h3>
      <p className="feed-links">
        <a href={f.url} target="_blank" rel="noreferrer">
          <Rss size={14} /> {f.url}
        </a>
        {f.site_url && (
          <a href={f.site_url} target="_blank" rel="noreferrer">
            <ExternalLink size={14} /> {f.site_url}
          </a>
        )}
      </p>
      <p className="feed-check">
        <UpdateNow data={data} act={act} feedId={f.id} checked={f.checked} />
        <span>
          {f.checked
            ? "Last checked " +
              ago(f.checked) +
              " ago · next in about " +
              f.interval +
              " min"
            : "Not checked yet"}
        </span>
      </p>
      {feedIssue(f) && <p className="inline-error">{feedIssue(f).note}</p>}
      <BackfillStatus
        jobs={data.backfills || []}
        feeds={data.feeds}
        scope={{ feed_id: f.id }}
        act={(...args) => act(...args).catch((e) => setError(e.message))}
      />
      {error && (
        <p className="inline-error" role="alert">
          {error}
        </p>
      )}
      <div className="form-grid">
        <Field label="Custom title">
          <input name="title" defaultValue={f.title} />
        </Field>
        <Field label="Folder">
          <FolderSelect
            data={data}
            act={act}
            name="folder_id"
            label="Folder"
            value={folderId}
            onChange={setFolderId}
          />
        </Field>
        <Field
          label="Refresh interval (minutes)"
          hint={
            "Leave blank for automatic · currently " + f.interval + " minutes"
          }
        >
          <input
            name="interval"
            type="number"
            min="5"
            max="10080"
            defaultValue={f.options.interval || ""}
            placeholder="Automatic"
          />
        </Field>
        <Field label="Feed tags">
          <input name="tags" defaultValue={f.tags.join(", ")} />
        </Field>
      </div>
      <label className="check-field">
        <input
          type="checkbox"
          name="bookmarked"
          defaultChecked={f.options.bookmarked}
        />
        <span>Bookmark this feed (listed under Bookmarked feeds)</span>
      </label>
      <label className="check-field">
        <input
          type="checkbox"
          name="archive"
          defaultChecked={f.options.archive}
        />
        <span>Keep this feed’s archive forever</span>
      </label>
      <label className="check-field">
        <input
          type="checkbox"
          name="extract"
          defaultChecked={f.options.extract ?? data.settings.extract_articles}
        />
        <span>Automatically fetch full articles</span>
      </label>
      <label className="check-field">
        <input
          type="checkbox"
          name="mark_read"
          defaultChecked={f.options.mark_read}
        />
        <span>Mark new articles as read</span>
      </label>
      <hr />
      <div className="form-grid">
        {[
          ["selector", "Full article CSS selector", "article"],
          [
            "scrape_selector",
            "Site scraper: story card selector",
            "Only for sites without feeds",
          ],
          [
            "transcript_selector",
            "Provided transcript CSS selector",
            ".transcript",
          ],
          [
            "backfill_url",
            "Archive feed URL",
            "https://example.com/archive.xml",
          ],
          ["user_agent", "Custom User-Agent", "Feedelio/0.1"],
          ["proxy", "HTTP proxy", "http://proxy:8080"],
        ].map(([name, label, placeholder]) => (
          <Field key={name} label={label}>
            <input
              name={name}
              defaultValue={f.options[name] || ""}
              placeholder={placeholder}
            />
          </Field>
        ))}
      </div>
      <Field
        label="Cookie header"
        hint="Uses your own existing session. Stored locally and included in full JSON backups."
      >
        <input
          name="cookie"
          type="password"
          autoComplete="off"
          defaultValue={f.options.cookie || ""}
        />
      </Field>
      <div className="form-actions">
        <button
          type="button"
          className="button danger-button"
          onClick={async () => {
            if (!confirm(`Unsubscribe from ${f.title}? You can undo this.`))
              return;
            const done = await submit(() =>
              act(
                "bulk_feeds",
                { ids: [f.id], action: "delete" },
                "Unsubscribed · Z to undo",
              ),
            );
            if (done) onBack();
          }}
        >
          <Trash2 size={16} />
          Unsubscribe
        </button>
        <div className="action-pair">
          <button
            type="button"
            onClick={() =>
              act(
                "enqueue",
                { kind: "backfill", payload: { feed_id: f.id } },
                "Archive backfill queued",
              ).catch(() => {})
            }
          >
            Backfill archive
          </button>
          <button className="primary">Save feed settings</button>
        </div>
      </div>
      <p className="muted">
        Last checked:{" "}
        {f.checked ? new Date(f.checked).toLocaleString() : "Not yet"}. Backfill
        automatically discovers exposed history (including Substack archives and
        YouTube channel pages), or uses your saved archive feed URL. One archive
        request per minute; progress and pause/resume are shown above these
        settings.
      </p>
    </form>
  );
}

// The worker's log: one collapsed frame with the backlog summary and the last
// thirty jobs, each with the time it last moved.
function JobsLog({ data, act, submit }) {
  const stamp = (value) =>
    new Date(value).toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  const subject = (j) =>
    j.payload.feed_id
      ? data.feeds.find((f) => f.id === j.payload.feed_id)?.title || "a feed"
      : j.payload.url
        ? j.payload.url.split("/").pop()?.slice(0, 50)
        : j.payload.id
          ? "an article"
          : "";
  return (
    <details className="jobs-log">
      <summary>Background jobs</summary>
      <QueueSummary queue={data.queue || {}} />
      {data.jobs.map((j) => (
        <div className="job-row" key={j.id}>
          <time dateTime={j.updated}>{stamp(j.updated)}</time>
          <span>
            {j.kind} {subject(j)}
            {j.error && <small>{j.error}</small>}
          </span>
          <span className={"job-" + j.status}>{j.status}</span>
          <RetryJob job={j} act={act} submit={submit} />
        </div>
      ))}
    </details>
  );
}

// What the worker is doing and has done in the last day, one line per kind of job.
function QueueSummary({ queue }) {
  const kinds = [
    ["refresh", "Feed updates", "updated"],
    ["backfill", "Archive backfills", "finished"],
    ["extract", "Full-text extractions", "extracted"],
    ["download", "Episode downloads", "downloaded"],
  ];
  const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
  return (
    <div className="queue-table">
      {kinds.map(([kind, label, verb]) => {
        const q = queue[kind] || {
          waiting: 0,
          done: 0,
          failed: 0,
          articles: 0,
        };
        return (
          <div key={kind}>
            <span>{label}</span>
            <span>
              {q.waiting ? `${q.waiting} waiting` : "Nothing waiting"} ·{" "}
              {q.done} {verb} in the last day
              {q.failed ? `, ${q.failed} failed` : ""}
              {kind === "backfill" && q.articles
                ? ` · ${plural(q.articles, "older article")} added so far`
                : ""}
            </span>
          </div>
        );
      })}
      <p className="muted">
        Each site is asked for one archive page a minute (YouTube every ten
        seconds), so backfills on different sites advance side by side.
      </p>
    </div>
  );
}

function DataSettings({ data, act, submit }) {
  const [notice, setNotice] = useState("");
  async function importFile(e, format) {
    const file = e.target.files[0];
    if (!file) return;
    await submit(async () => {
      const text = await file.text();
      const result = await act(
        format === "opml" ? "import_opml" : "restore",
        format === "opml" ? { xml: text } : { backup: JSON.parse(text) },
        format === "opml" ? "Subscriptions imported" : "Backup restored",
      );
      setNotice(
        format === "opml"
          ? result.imported +
              " subscriptions imported." +
              (result.unresolved
                ? " " +
                  result.unresolved +
                  " host" +
                  (result.unresolved === 1 ? "" : "s") +
                  " could not be resolved; those feeds will show a refresh error."
                : "")
          : result.restored + " articles restored.",
      );
    });
    e.target.value = "";
  }
  return (
    <div className="data-settings">
      <div
        className="opml-drop"
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => {
          e.preventDefault();
          importFile(
            { target: { files: e.dataTransfer.files, value: "" } },
            "opml",
          );
        }}
      >
        <h3>Drop an OPML file</h3>
        <p>
          or{" "}
          <label className="file-button">
            choose a file
            <input
              aria-label="Import OPML"
              type="file"
              accept=".opml,.xml"
              onChange={(e) => importFile(e, "opml")}
            />
          </label>{" "}
          · from Feedbin, Inoreader, NetNewsWire
        </p>
      </div>
      {notice && <p role="status">Last import: {notice}</p>}
      <div className="data-export-row">
        <div>
          <span>Export subscriptions</span>
          <small>Feeds and folders, ready for another reader.</small>
        </div>
        <a className="button" href="/api/export/opml" download>
          Export OPML
        </a>
      </div>
      <div className="data-export-row">
        <div>
          <span>Export starred</span>
          <small>Your saved articles, as JSON.</small>
        </div>
        <a className="button" href="/api/export/starred" download>
          Export JSON
        </a>
      </div>
      <div className="data-export-row">
        <div>
          <span>Remove every feed</span>
          <small>
            Remove subscriptions from this library. Undo is available.
          </small>
        </div>
        <button
          className="button danger-button"
          disabled={!data.feeds.length}
          onClick={() => {
            if (
              confirm(
                `Remove all ${data.feeds.length} feeds? You can undo this change.`,
              )
            )
              submit(() =>
                act(
                  "bulk_feeds",
                  { ids: data.feeds.map((f) => f.id), action: "delete" },
                  "All feeds removed · Z to undo",
                ),
              );
          }}
        >
          Remove all
        </button>
      </div>
      <div className="data-export-row">
        <div>
          <span>Your entire library, in one file</span>
          <small>
            Articles, folders, tags, saved items, settings, history, rules and
            downloaded episodes. Restore into an empty library. Audio makes this
            file larger.
          </small>
        </div>
        <div className="row-actions">
          <a className="button" href="/api/export/json" download>
            <Download size={16} />
            Full JSON backup
          </a>
          <label className="file-button">
            Restore JSON
            <input
              type="file"
              accept=".json"
              disabled={!!data.feeds.length}
              onChange={(e) => importFile(e, "json")}
            />
          </label>
        </div>
      </div>
      <form
        className="data-card"
        onSubmit={(e) => {
          e.preventDefault();
          const p = Object.fromEntries(new FormData(e.currentTarget));
          submit(() =>
            act(
              "save_settings",
              {
                values: {
                  retention_days: Number(p.retention_days),
                  rsshub: p.rsshub,
                  invidious: p.invidious,
                  new_feed_read: p.new_feed_read === "on",
                  extract_articles: p.extract_articles === "on",
                },
              },
              "Library settings saved",
            ),
          );
        }}
      >
        <Field
          label="Retain read articles for (days)"
          hint="0 (the default) keeps everything. Unread, saved and archive-feed articles are protected."
        >
          <input
            name="retention_days"
            type="number"
            min="0"
            max="36500"
            defaultValue={data.settings.retention_days}
          />
        </Field>
        <label className="check-field">
          <input
            type="checkbox"
            name="new_feed_read"
            defaultChecked={data.settings.new_feed_read}
          />
          <span>
            Start new subscriptions read. Existing posts and backfilled history
            are marked read; only posts published later count as unread.
          </span>
        </label>
        <label className="check-field">
          <input
            type="checkbox"
            name="extract_articles"
            defaultChecked={data.settings.extract_articles}
          />
          <span>
            Automatically fetch full articles for feeds that do not set their
            own preference.
          </span>
        </label>
        <Field label="RSSHub instance">
          <input name="rsshub" type="url" defaultValue={data.settings.rsshub} />
        </Field>
        <Field
          label="Invidious instance (optional)"
          hint="Leave blank to use youtube-nocookie.com. Videos load only when you press play."
        >
          <input
            name="invidious"
            type="url"
            defaultValue={data.settings.invidious}
            placeholder="https://your-invidious.example"
          />
        </Field>
        <button className="primary">Save settings</button>
      </form>
      <div className="data-card">
        <h3>Chrome & browser integration</h3>
        <p>
          Load the companion extension from the <code>extension</code> directory
          using Chrome’s “Load unpacked”. Set this server’s URL and access token
          in its options. Its toolbar button subscribes to the current page.
          While Chrome is open, its Reading List shows under Reading list in the
          tray: read state syncs both ways within half a minute, and a page
          removed from the list in Chrome leaves Feedelio too.
        </p>
        <p>
          <strong>Server URL:</strong> <code>{location.origin}</code>
        </p>
      </div>
      <div className="data-card">
        <h3>Connect an AI reader</h3>
        <p>
          The included MCP server exposes search, articles, subscriptions and
          read/save actions over stdio. Configure your MCP client to run{" "}
          <code>uv run feedelio-mcp</code> in this project with the same{" "}
          <code>FEEDELIO_DATA</code> directory.
        </p>
      </div>
      <p className="privacy-note">
        <ShieldCheck size={14} />
        No analytics. No shared account. Your reading belongs to you.
      </p>
    </div>
  );
}

createRoot(document.getElementById("root")).render(
  <QueryClientProvider client={queryClient}>
    <App />
  </QueryClientProvider>,
);
