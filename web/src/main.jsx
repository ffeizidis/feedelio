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
  FolderPlus,
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
  Eye,
  EyeOff,
  Bookmark,
  Image,
  Loader2,
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
function elapsed(value) {
  if (!value) return "never";
  const minutes = Math.max(0, (Date.now() - new Date(value).getTime()) / 60000);
  return minutes < 1
    ? "just now"
    : minutes < 60
      ? `${Math.floor(minutes)}m`
      : minutes < 1440
        ? `${Math.floor(minutes / 60)}h`
        : minutes < 10080
          ? `${Math.floor(minutes / 1440)}d`
          : `${Math.floor(minutes / 10080)}w`;
}
function feedIssue(f) {
  if (f.error)
    return {
      kind: "error",
      label: `failed ${elapsed(f.checked)}`,
      note: `${f.failures} failed fetches: ${f.error}`,
    };
  if (
    Date.now() - new Date(f.last_article || f.created).getTime() >=
    14 * 86400000
  )
    return {
      kind: "stale",
      label: `stale ${elapsed(f.last_article || f.created)}`,
      note: "No new items in 14 days or more",
    };
  if (f.partial_items)
    return {
      kind: "partial",
      label: "partial",
      note: `${f.partial_items} articles are paywalled or archive previews; full text may need a subscription`,
    };
  return null;
}
const feedStatuses = {
  all: "All feeds",
  healthy: "Healthy",
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
  if (!video && !paywall) return null;
  return (
    <span className="row-marks">
      {video && short && <Smartphone size={12} aria-label="YouTube Short" />}
      {video && !short && <Video size={12} aria-label="Video" />}
      {paywall && <Lock size={12} aria-label="Paywalled" />}
    </span>
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
      onClick={() =>
        act(
          "enqueue",
          {
            kind: "refresh",
            payload: feedId ? { feed_id: feedId, manual: true } : {},
          },
          "Update queued",
        )
      }
    >
      <RefreshCw size={13} className={pending ? "spin" : ""} />
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
          Archive {j.status === "queued" ? "waiting" : j.status} ·{" "}
          {j.result?.pages || 0} pages · {j.result?.articles || 0} older
          articles added
          {!!j.result?.previews && (
            <span> · {j.result.previews} available as preview only</span>
          )}
          {j.status === "queued" && (
            <span>
              {" "}
              ·{" "}
              {j.result?.not_before
                ? "next request after " +
                  new Date(j.result.not_before).toLocaleTimeString()
                : "one request per minute"}
            </span>
          )}
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
  const [view, setView] = useState(resume.view || "all"),
    [scope, setScope] = useState(resume.scope || {}),
    [q, setQ] = useState(resume.q || ""),
    [offset, setOffset] = useState(resume.offset || 0);
  const [selected, setSelected] = useState(resume.selected || null),
    [collapsed, setCollapsed] = useState({}),
    [modal, setModal] = useState(
      new URLSearchParams(location.search).has("subscribe")
        ? "subscribe"
        : null,
    );
  const [manageFeed, setManageFeed] = useState(null);
  const [toast, setToast] = useState(null),
    [busy, setBusy] = useState(0),
    [loadImages, setLoadImages] = useState(false),
    [reloadPolicy, setReloadPolicy] = useState(false);
  const [token, setToken] = useState("");
  const [paneWidth, setPaneWidth] = useState(null);
  const [listWidth, setListWidth] = useState(null),
    [searchOpen, setSearchOpen] = useState(!!resume.q),
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
  const layers = useRef({ stack: [], skip: 0, close: {} });
  useLayer(layers, "article", !!selected, () => setSelected(null));
  useLayer(layers, "modal", !!modal, () => setModal(null));
  useEffect(() => {
    const pop = () => {
      const state = layers.current;
      if (state.skip) state.skip--;
      else state.close[state.stack.pop()]?.();
    };
    window.addEventListener("popstate", pop);
    return () => window.removeEventListener("popstate", pop);
  }, []);
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
    refetchInterval: 10000,
  });
  // The tab names what is open: the article, the library, or the stream.
  const tabTitle =
    selected && article.data?.title
      ? article.data.title
      : modal
        ? "Manage library"
        : q
          ? "Search results"
          : data?.feeds.find((f) => f.id === scope.feed_id)?.title ||
            data?.folders.find((f) => f.id === scope.folder_id)?.name ||
            {
              all: "All articles",
              starred: "Saved for later",
              bookmarks: "Bookmarked feeds",
              history: "Recently read",
            }[view];
  useEffect(() => {
    if (data) document.title = "feedelio - " + tabTitle;
  }, [tabTitle, data]);
  const items = articles.data?.items || [],
    a = article.data;
  // The hide-read preference can arrive after the first keypress. Reset on the
  // user's toggle, not on its asynchronous response, so it cannot erase history.
  const navigationKey = JSON.stringify([
    { ...params, unread: undefined },
    collapsed,
  ]);
  const visibleItems = items;
  const refresh = () => cache.invalidateQueries();
  const notify = (message, error = false) => setToast({ message, error });
  async function act(name, payload, message) {
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
      await refresh();
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
    if (settings.open_titles_in === "original")
      window.open(item.url, "_blank", "noopener,noreferrer");
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
      if (
        modal ||
        ["INPUT", "TEXTAREA", "SELECT"].includes(e.target.tagName) ||
        e.metaKey ||
        e.ctrlKey ||
        e.altKey
      )
        return;
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
        if (e.shiftKey)
          window.open(target.url, "_blank", "noopener,noreferrer");
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
            viewport - sidebarSize - 300,
            (listWidth ?? settings.list_width ?? 590) +
              (e.key === "[" ? -20 : 20),
          ),
        );
        setListWidth(width);
        pref("list_width", width);
      } else if (e.key === "n" || e.key === "p") {
        const index = data.feeds.findIndex((f) => f.id === scope.feed_id);
        const feed =
          data.feeds[
            Math.max(
              0,
              Math.min(data.feeds.length - 1, index + (e.key === "n" ? 1 : -1)),
            )
          ];
        if (feed) stream("all", { feed_id: feed.id });
      } else if (e.key === "o" && a)
        window.open(a.url, "_blank", "noopener,noreferrer");
      else if (e.key === "r")
        run(
          "enqueue",
          {
            kind: "refresh",
            payload: scope.feed_id
              ? { feed_id: scope.feed_id, manual: true }
              : {},
          },
          "Update queued",
        );
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
        <Rss size={30} />
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
  const heading = q
    ? "Search results"
    : activeFeed?.title ||
      activeFolder?.name ||
      {
        all: "All articles",
        starred: "Saved for later",
        bookmarks: "Bookmarked feeds",
        history: "Recently read",
      }[view];
  const count = articles.data?.total || 0;
  const archive =
    activeFeed &&
    (data.backfills || []).find((j) => j.payload.feed_id === activeFeed.id);
  const sidebarSize = Math.max(
    180,
    Math.min(
      paneWidth ?? settings.sidebar_width ?? 252,
      Math.max(180, viewport - 680),
    ),
  );
  const listSize = Math.max(
    300,
    Math.min(
      listWidth ?? settings.list_width ?? 590,
      Math.max(300, viewport - sidebarSize - 300),
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
  const shown = (f) => !unreadOnly || f.unread > 0 || scope.feed_id === f.id;
  const folderTree = (parent = null, depth = 0) =>
    data.folders
      .filter(
        (f) =>
          f.parent_id === parent &&
          (!unreadOnly || f.unread > 0 || keep.has(f.id)),
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
              onClick={() =>
                setCollapsed({
                  ...collapsed,
                  ["nav" + f.id]: !collapsed["nav" + f.id],
                })
              }
            >
              {collapsed["nav" + f.id] ? (
                <ChevronRight size={13} />
              ) : (
                <ChevronDown size={13} />
              )}
            </button>
            <button onClick={() => stream("all", { folder_id: f.id })}>
              <Folder size={15} />
              <span>{f.name}</span>
              <span className="count" data-unread={f.unread}>
                {f.unread || ""}
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
                    onClick={() => stream("all", { feed_id: f.id })}
                  >
                    <i className="hue-dot" />
                    <span>{f.title}</span>
                    {f.health === "error" && (
                      <AlertCircle size={13} className="error-color" />
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

  return (
    <main
      className="shell"
      style={{
        "--sidebar-width": sidebarSize + "px",
        "--list-width": listSize + "px",
      }}
    >
      <aside className="sidebar" aria-label="Library">
        <header className="brand">
          <a href="/" aria-label="Feedelio home">
            <span
              className={
                settings.wordmark === "plain" ? "plain-wordmark" : "script-f"
              }
            >
              f
            </span>
            <span className="wordmark-rest">eedelio</span>
          </a>
          <span className="private-label">
            <ShieldCheck size={12} /> JUST FOR YOU
          </span>
        </header>
        <div className="library-controls">
          <button
            className="search-trigger"
            onClick={() => setSearchOpen(true)}
          >
            Search <span>/</span>
          </button>
          <nav className="views" aria-label="Streams">
            {[
              ["all", Inbox, "All articles", data.counts.unread],
              ["starred", Star, "Saved", data.counts.starred],
              [
                "bookmarks",
                Bookmark,
                "Bookmarked feeds",
                data.counts.bookmarks,
              ],
              ["history", Clock3, "History", null],
            ].map(([id, Icon, label, n]) => (
              <button
                key={id}
                className={
                  view === id && !scope.feed_id && !scope.folder_id
                    ? "active"
                    : ""
                }
                onClick={() => stream(id)}
              >
                <Icon size={16} />
                <span>{label}</span>
                {n !== null && (
                  <span className="pill" data-unread={n}>
                    {n}
                  </span>
                )}
              </button>
            ))}
            <button onClick={() => setModal("feeds")}>
              <Settings2 size={16} />
              <span>Manage library</span>
            </button>
          </nav>
          <div className="subscriptions-heading">
            <button
              onClick={() =>
                setCollapsed({ ...collapsed, library: !collapsed.library })
              }
            >
              <ChevronDown
                size={13}
                className={collapsed.library ? "rotate" : ""}
              />{" "}
              FEEDS
            </button>
            <div>
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
              <IconButton
                icon={FolderPlus}
                label="New folder"
                onClick={() => setModal("new-folder")}
              />
              <IconButton
                icon={Plus}
                label="Add a subscription"
                onClick={() => setModal("subscribe")}
              />
            </div>
          </div>
          {!collapsed.library && (
            <div className="folder-tree">
              {data.feeds.length ? (
                folderTree()
              ) : (
                <button
                  className="add-first"
                  onClick={() => setModal("subscribe")}
                >
                  <Plus size={16} /> Add your first subscription
                </button>
              )}
            </div>
          )}
        </div>
      </aside>
      <section className="stream-pane" aria-label="Article stream">
        <ResizeHandle
          label="Resize feeds pane"
          value={sidebarSize}
          min={180}
          max={Math.min(600, viewport - listSize - 300)}
          onChange={setPaneWidth}
          onSave={(v) => {
            setPaneWidth(v);
            pref("sidebar_width", v);
          }}
        />
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
            <h1>
              <button
                className="heading-link"
                title="Feed settings"
                aria-label={"Manage feed: " + activeFeed.title}
                onClick={() => {
                  setManageFeed(activeFeed.id);
                  setModal("feeds");
                }}
              >
                {heading}
              </button>
            </h1>
          ) : (
            <h1>{heading}</h1>
          )}
          <div className="stream-meta">
            <span>
              {count.toLocaleString()}{" "}
              {searchOpen ? "results" : count === 1 ? "article" : "articles"} ·{" "}
              {articles.data?.unread || 0} unread
            </span>
            <UpdateNow
              data={data}
              act={run}
              feedId={scope.feed_id}
              checked={activeFeed?.checked}
            />
            <Switch
              label="Unread only"
              checked={!!settings.hide_read}
              onChange={(v) => pref("hide_read", v)}
            />
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
          {archive && !searchOpen && (
            <button
              className="archive-progress"
              aria-label="Archive progress"
              onClick={() => {
                setManageFeed(activeFeed.id);
                setModal("feeds");
              }}
            >
              {archive.status === "running" && (
                <Loader2 size={13} className="spin" />
              )}
              Archive{" "}
              {
                {
                  queued: "in progress",
                  running: "in progress",
                  paused: "paused",
                  failed: "failed",
                  done: "complete",
                }[archive.status]
              }{" "}
              · {archive.result?.pages || 0} pages ·{" "}
              {archive.result?.articles || 0} older articles
              {archive.status === "queued" && archive.result?.not_before && (
                <>
                  {" "}
                  · next page after{" "}
                  {new Date(archive.result.not_before).toLocaleTimeString()}
                </>
              )}
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
          <div className="stream-utilities">
            <button
              onClick={() =>
                pref("sort", settings.sort === "oldest" ? "newest" : "oldest")
              }
            >
              <ArrowUpDown size={13} />
              {settings.sort === "oldest" ? "Oldest first" : "Newest first"}
            </button>
          </div>
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
              <Leaf size={27} />
              <h3>
                {q
                  ? "No matches yet"
                  : data.feeds.length
                    ? "A quiet moment."
                    : "A fresh start."}
              </h3>
              <p>
                {q
                  ? "Try a different word or turn off unread only."
                  : data.feeds.length
                    ? "You’re all caught up here. New articles will arrive automatically."
                    : "The things you love to read will find a home here."}
              </p>
              {!data.feeds.length && (
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
                  <button
                    className="read-toggle"
                    aria-label={
                      (item.read ? "Mark unread: " : "Mark read: ") + item.title
                    }
                    onClick={() =>
                      run("change_articles", {
                        ids: [item.id],
                        read: !item.read,
                      })
                    }
                  >
                    <span
                      className={"read-circle " + (item.read ? "is-read" : "")}
                    />
                  </button>
                  <button
                    className={"star-toggle " + (item.starred ? "starred" : "")}
                    aria-label={
                      (item.starred ? "Unstar: " : "Star: ") + item.title
                    }
                    aria-pressed={!!item.starred}
                    onClick={() =>
                      run("change_articles", {
                        ids: [item.id],
                        starred: !item.starred,
                      })
                    }
                  >
                    ★
                  </button>
                </div>
                {!scope.feed_id && (
                  <button
                    className="row-feed"
                    onClick={() => stream("all", { feed_id: item.feed_id })}
                  >
                    {item.feed_title}
                  </button>
                )}
                <button className="row-title" onClick={() => openTitle(item)}>
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
          max={Math.min(1000, viewport - sidebarSize - 300)}
          start={sidebarSize}
          onChange={setListWidth}
          onSave={(v) => {
            setListWidth(v);
            pref("list_width", v);
          }}
        />
        {!selected ? (
          <div className="reader-empty">
            <BookOpen size={26} />
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
                    <button
                      onClick={() => stream("all", { feed_id: a.feed_id })}
                    >
                      {a.feed_title}
                    </button>
                    <span className="kicker-sep">·</span>
                    <time dateTime={a.published}>{fullDate(a.published)}</time>
                  </div>
                  <details className="article-details">
                    <summary>
                      {a.duration
                        ? `${clock(a.duration)} video`
                        : `${a.reading_minutes} min read · ${a.words.toLocaleString()} words`}
                    </summary>
                    {a.author && <p>By {a.author}</p>}
                  </details>
                  <div className="reader-meta">
                    <span className="reader-meta-actions">
                      <button
                        className="read-toggle"
                        aria-label={
                          a.read ? "Mark article unread" : "Mark article read"
                        }
                        onClick={() =>
                          run("change_articles", { ids: [a.id], read: !a.read })
                        }
                      >
                        <span
                          className={"read-circle " + (a.read ? "is-read" : "")}
                        />
                      </button>
                      <button
                        className={
                          "star-toggle " + (a.starred ? "starred" : "")
                        }
                        aria-label={
                          a.starred ? "Unsave article" : "Save for later"
                        }
                        aria-pressed={!!a.starred}
                        onClick={() =>
                          run("change_articles", {
                            ids: [a.id],
                            starred: !a.starred,
                          })
                        }
                      >
                        ★
                      </button>
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
                    .map((e) => (
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
                            <Download size={15} />
                            Download
                          </button>
                        </div>
                        <audio
                          controls
                          preload="none"
                          src={
                            a.downloads.find(
                              (d) => d.status === "done" && d.url === e.href,
                            )
                              ? "/api/downloads/" +
                                a.downloads.find(
                                  (d) =>
                                    d.status === "done" && d.url === e.href,
                                ).id
                              : e.href
                          }
                        />
                      </div>
                    ))}
                  {!settings.images &&
                    !loadImages &&
                    a.body.includes("<img") && (
                      <button
                        className="image-notice"
                        onClick={() => setLoadImages(true)}
                      >
                        <Image size={15} />
                        Images are paused. Load images for this article.
                      </button>
                    )}
                  {a.body ? (
                    <SafeArticle
                      body={a.body}
                      images={settings.images || loadImages}
                    />
                  ) : (
                    <div className="no-body">
                      <FileText size={27} />
                      <p>This feed provided a link without article text.</p>
                      <button
                        className="primary"
                        onClick={() =>
                          run(
                            "enqueue",
                            { kind: "extract", payload: { article_id: a.id } },
                            "Full article extraction queued",
                          )
                        }
                      >
                        Fetch full article <ArrowUpRight size={15} />
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
                    <button
                      onClick={() =>
                        run(
                          "enqueue",
                          { kind: "extract", payload: { article_id: a.id } },
                          "Full article extraction queued",
                        )
                      }
                    >
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
          {toast.error ? <AlertCircle size={17} /> : <Check size={17} />}
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
      {modal === "new-folder" && (
        <FolderSheet
          data={data}
          act={act}
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
                  tags: new FormData(e.currentTarget)
                    .get("tags")
                    .split(",")
                    .map((s) => s.trim())
                    .filter(Boolean),
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
      {[
        "feeds",
        "appearance",
        "folders",
        "rules",
        "data",
        "statistics",
        "downloads",
      ].includes(modal) && (
        <Library
          initial={modal}
          initialFeed={manageFeed}
          layers={layers}
          pref={pref}
          data={data}
          act={act}
          onClose={() => {
            setModal(null);
            setManageFeed(null);
          }}
          onSubscribe={() => setModal("subscribe")}
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
    [loading, setLoading] = useState(false),
    [error, setError] = useState(""),
    [advanced, setAdvanced] = useState(false);
  const [folder, setFolder] = useState("inbox"),
    [title, setTitle] = useState(""),
    [selector, setSelector] = useState(""),
    [bridge, setBridge] = useState("");
  async function find(e) {
    e.preventDefault();
    setLoading(true);
    setError("");
    try {
      const result = await cmd("discover", { url });
      setLinks(result);
      setChosen(result[0]?.url || "");
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }
  async function add(feedUrl) {
    setLoading(true);
    setError("");
    try {
      await act(
        "subscribe",
        {
          url: feedUrl,
          folder_id: folder,
          title,
          options: selector ? { scrape_selector: selector, extract: true } : {},
        },
        "Subscription added. The worker will fetch it shortly.",
      );
      onClose();
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
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
  layers,
  data,
  act,
  pref,
  onClose,
  onSubscribe,
}) {
  const [tab, setTab] = useState(initial),
    [ids, setIds] = useState([]),
    [edit, setEdit] = useState(
      () => data.feeds.find((f) => f.id === initialFeed) || null,
    ),
    [status, setStatus] = useState("all"),
    [fromList, setFromList] = useState(false),
    [error, setError] = useState("");
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
  }, []);
  const [settingsWidth, setSettingsWidth] = useState(
    data.settings.sidebar_width,
  );
  const rules = useQuery({ queryKey: ["rules"], queryFn: () => api("rules") });
  const stats = useQuery({
    queryKey: ["statistics"],
    queryFn: () => api("statistics"),
    enabled: tab === "statistics",
  });
  async function submit(fn) {
    setError("");
    try {
      await fn();
    } catch (e) {
      setError(e.message);
    }
  }
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
      <aside className="settings-sidebar">
        <button className="back-to-reading" onClick={onClose}>
          ← All articles
        </button>
        <h2 className="section-label">Manage library</h2>
        <nav className="dialog-tabs" aria-label="Library settings">
          {[
            ["feeds", "Feeds"],
            ["folders", "Folders"],
            ["appearance", "Appearance"],
            ["keyboard", "Keyboard"],
            ["data", "Import & export"],
            ["rules", "Rules"],
            ["downloads", "Downloads"],
            ["statistics", "Activity"],
          ].map(([id, label]) => (
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
                      {lastRefreshed
                        ? `refreshed ${elapsed(lastRefreshed)} ago`
                        : "not refreshed yet"}
                    </span>
                    <select
                      aria-label="Filter feeds by status"
                      value={status}
                      onChange={(e) => setStatus(e.target.value)}
                    >
                      {Object.entries(feedStatuses).map(([id, label]) => (
                        <option key={id} value={id}>
                          {label}
                          {id !== "all" &&
                            ` (${data.feeds.filter((f) => (feedIssue(f)?.kind || "healthy") === id).length})`}
                        </option>
                      ))}
                    </select>
                    <button className="primary" onClick={onSubscribe}>
                      <Plus size={15} />
                      Add feed
                    </button>
                  </div>
                  {ids.length > 0 && (
                    <div className="bulk-bar">
                      <strong>{ids.length} selected</strong>
                      <select
                        aria-label="Move selected feeds"
                        defaultValue=""
                        onChange={(e) =>
                          submit(() =>
                            act(
                              "bulk_feeds",
                              {
                                ids,
                                action: "move",
                                folder_id: e.target.value,
                              },
                              "Feeds moved",
                            ),
                          )
                        }
                      >
                        <option value="" disabled>
                          Move to folder…
                        </option>
                        {data.folders.map((f) => (
                          <option key={f.id} value={f.id}>
                            {f.name}
                          </option>
                        ))}
                      </select>
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
                        <RefreshCw size={15} />
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
                        <Trash2 size={15} />
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
                                tags: new FormData(e.currentTarget)
                                  .get("tags")
                                  .split(",")
                                  .map((s) => s.trim())
                                  .filter(Boolean),
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
                      .filter(
                        (f) =>
                          status === "all" ||
                          (feedIssue(f)?.kind || "healthy") === status,
                      )
                      .map((f) => (
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
                              (feedIssue(f)?.kind ||
                                (f.checked ? "healthy" : ""))
                            }
                          >
                            {feedIssue(f)?.label || elapsed(f.checked)}
                          </span>
                          <span>
                            {feedIssue(f) && (
                              <span
                                className={"feed-issue " + feedIssue(f).kind}
                                title={feedIssue(f).note}
                                aria-label={feedIssue(f).note}
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
                      ))}
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
            <>
              <p className="muted">
                Rules run on new articles and extracted content, in the order
                below. Patterns are case-insensitive regular expressions.
              </p>
              <div className="rule-presets">
                <button
                  onClick={() =>
                    submit(() =>
                      act(
                        "save_rule",
                        {
                          id: "shorts",
                          name: "Skip YouTube Shorts",
                          field: "url",
                          pattern: "youtube\\.com/shorts/",
                          action: "read",
                        },
                        "Shorts rule added",
                      ),
                    )
                  }
                >
                  + Skip YouTube Shorts
                </button>
                <button
                  onClick={() =>
                    submit(() =>
                      act(
                        "save_rule",
                        {
                          id: "paywall",
                          name: "Skip paywalled articles",
                          field: "tags",
                          pattern: "paywall",
                          action: "read",
                        },
                        "Paywall rule added",
                      ),
                    )
                  }
                >
                  + Mark paywalls as read
                </button>
              </div>
              {rules.data?.map((r) => (
                <div className="rule-row" key={r.id}>
                  <input
                    aria-label={"Enable " + r.name}
                    type="checkbox"
                    checked={!!r.enabled}
                    onChange={(e) =>
                      submit(() =>
                        act("save_rule", { ...r, enabled: e.target.checked }),
                      )
                    }
                  />
                  <span>
                    <strong>{r.name}</strong>
                    <small>
                      {r.field} matches /{r.pattern}/ → {r.action} {r.value}
                    </small>
                  </span>
                  <IconButton
                    icon={Trash2}
                    label={"Delete " + r.name}
                    onClick={() =>
                      submit(() => act("delete_rule", { id: r.id }))
                    }
                  />
                </div>
              ))}
              <form
                className="rule-form"
                onSubmit={(e) => {
                  e.preventDefault();
                  const form = e.currentTarget,
                    p = Object.fromEntries(new FormData(form));
                  p.feed_id = p.feed_id || null;
                  submit(async () => {
                    await act("save_rule", p, "Rule added");
                    form.reset();
                  });
                }}
              >
                <Field label="Rule name">
                  <input
                    name="name"
                    required
                    placeholder="Keep the good stuff"
                  />
                </Field>
                <div className="form-grid">
                  <Field label="Match field">
                    <select name="field">
                      {["title", "body", "url", "author", "tags"].map((x) => (
                        <option key={x}>{x}</option>
                      ))}
                    </select>
                  </Field>
                  <Field label="Pattern (regex)">
                    <input
                      name="pattern"
                      required
                      placeholder="subscribe to continue"
                    />
                  </Field>
                  <Field label="Action">
                    <select name="action">
                      {["read", "star", "tag", "drop", "rewrite"].map((x) => (
                        <option key={x}>{x}</option>
                      ))}
                    </select>
                  </Field>
                  <Field label="Tag or replacement">
                    <input name="value" placeholder="Optional" />
                  </Field>
                </div>
                <Field label="Scope">
                  <select name="feed_id">
                    <option value="">All feeds</option>
                    {data.feeds.map((f) => (
                      <option key={f.id} value={f.id}>
                        {f.title}
                      </option>
                    ))}
                  </select>
                </Field>
                <div className="form-actions">
                  <button
                    type="button"
                    onClick={() =>
                      submit(() =>
                        act(
                          "enqueue",
                          { kind: "rules" },
                          "Rules queued for all stored articles",
                        ),
                      )
                    }
                  >
                    Apply rules to existing articles
                  </button>
                  <button className="primary">Add rule</button>
                </div>
              </form>
            </>
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
                        <Download size={17} />
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
                  {j.status === "failed" && (
                    <button
                      onClick={() =>
                        submit(() =>
                          act(
                            "enqueue",
                            { kind: "download", payload: j.payload },
                            "Download queued again",
                          ),
                        )
                      }
                    >
                      Retry
                    </button>
                  )}
                </div>
              ))}
              {!downloads.length && (
                <div className="list-empty">
                  <Headphones size={28} />
                  <h3>A listening queue, at your pace.</h3>
                  <p>Downloaded podcast episodes will appear here.</p>
                </div>
              )}
              <h3>Background jobs</h3>
              <QueueSummary queue={data.queue || {}} />
              {data.jobs
                .filter((j) => j.status === "failed")
                .map((j) => (
                  <div className="inline-error" key={j.id}>
                    {j.kind}: {j.error}
                    <button
                      onClick={() =>
                        submit(() =>
                          act(
                            "enqueue",
                            { kind: j.kind, payload: j.payload },
                            "Job queued again",
                          ),
                        )
                      }
                    >
                      Retry
                    </button>
                  </div>
                ))}
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
  const [error, setError] = useState(""),
    [folderId, setFolderId] = useState(f.folder_id);
  async function save(e) {
    e.preventDefault();
    const p = Object.fromEntries(new FormData(e.currentTarget));
    try {
      await act(
        "edit_feed",
        {
          id: f.id,
          title: p.title,
          folder_id: p.folder_id,
          tags: p.tags
            .split(",")
            .map((s) => s.trim())
            .filter(Boolean),
          options: {
            ...f.options,
            interval: p.interval ? Number(p.interval) : null,
            archive: p.archive === "on",
            bookmarked: p.bookmarked === "on",
            extract: p.extract === "on",
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
      );
      onBack();
    } catch (err) {
      setError(err.message);
    }
  }
  return (
    <form onSubmit={save}>
      <button type="button" className="text-button" onClick={onBack}>
        <ChevronLeft size={15} />
        Subscriptions
      </button>
      <h3 className="feed-editor-title">
        <Favicon feed={f} enabled={data.settings.images} />
        {f.title}
      </h3>
      <p className="feed-links">
        <a href={f.url} target="_blank" rel="noreferrer">
          <Rss size={13} /> {f.url}
        </a>
        {f.site_url && (
          <a href={f.site_url} target="_blank" rel="noreferrer">
            <ExternalLink size={13} /> {f.site_url}
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
        Each site is asked for one archive page a minute, so backfills on
        different sites advance side by side.
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
      <div className="data-card">
        <div>
          <h3>Your entire library, in one file</h3>
          <p>
            Articles, folders, tags, saved items, settings, history, rules and
            downloaded episodes. Restore into an empty library. Audio makes this
            file larger.
          </p>
        </div>
        <a className="button" href="/api/export/json" download>
          <Download size={15} />
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
          in its options. Its toolbar button subscribes to the current page;
          Reading List sync runs every minute while Chrome is open.
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
