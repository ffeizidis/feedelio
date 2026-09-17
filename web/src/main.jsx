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
  Keyboard,
  ArrowUpRight,
  Trash2,
  Download,
  Undo2,
  Circle,
  CheckCircle2,
  MoreHorizontal,
  Headphones,
  Play,
  FileText,
  ShieldCheck,
  SlidersHorizontal,
  Inbox,
  AlertCircle,
  Leaf,
  Image,
  Loader2,
} from "lucide-react";
import "./style.css";

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
const date = (value) =>
  new Date(value).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
  });
const fullDate = (value) =>
  new Date(value).toLocaleDateString(undefined, {
    month: "long",
    day: "numeric",
    year: "numeric",
  });
function IconButton({ icon: Icon, label, ...props }) {
  return (
    <button className="icon-button" title={label} aria-label={label} {...props}>
      <Icon size={18} />
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
      onCancel={onClose}
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
  const [toast, setToast] = useState(null),
    [busy, setBusy] = useState(0),
    [loadImages, setLoadImages] = useState(false),
    [playVideo, setPlayVideo] = useState(!!resume.playVideo),
    [reloadPolicy, setReloadPolicy] = useState(false);
  const [token, setToken] = useState("");
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
  const items = articles.data?.items || [],
    a = article.data;
  // The hide-read preference can arrive after the first keypress. Reset on the
  // user's toggle, not on its asynchronous response, so it cannot erase history.
  const navigationKey = JSON.stringify([
    { ...params, unread: undefined },
    collapsed,
  ]);
  const groups = new Map();
  for (const item of items) {
    if (!groups.has(item.folder_id)) groups.set(item.folder_id, []);
    groups.get(item.folder_id).push(item);
  }
  const visibleItems = [...groups].flatMap(([id, group]) =>
    collapsed["list" + id] ? [] : group,
  );
  const navigationItems =
    navigation.current.key === navigationKey
      ? navigation.current.items
      : visibleItems;
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
    setLoadImages(false);
    setPlayVideo(false);
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
    setView(nextView);
    setScope(nextScope);
    setOffset(0);
  }
  useEffect(() => {
    if (reloadPolicy && !modal) {
      try {
        sessionStorage.setItem(
          "feedelio-policy-resume",
          JSON.stringify({ view, scope, q, offset, selected, playVideo }),
        );
      } catch {
        /* Reload remains safe without storage. */
      }
      location.reload();
    }
  }, [reloadPolicy, modal]);
  useEffect(() => {
    document.documentElement.dataset.theme = settings.theme || "system";
  }, [settings.theme]);
  useEffect(() => {
    setOffset(0);
  }, [deferredQ, settings.hide_read, settings.sort]);
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
      if (
        modal ||
        ["INPUT", "TEXTAREA", "SELECT"].includes(e.target.tagName) ||
        e.metaKey ||
        e.ctrlKey ||
        e.altKey
      )
        return;
      if (e.key === "j" || e.key === "ArrowDown") {
        e.preventDefault();
        navigate(1);
      } else if (e.key === "k" || e.key === "ArrowUp") {
        e.preventDefault();
        navigate(-1);
      } else if (e.key === "/" || e.key === "f") {
        e.preventDefault();
        searchRef.current?.focus();
      } else if (e.key === "s" && a)
        run("change_articles", { ids: [a.id], starred: !a.starred });
      else if (e.key === "m" && a)
        run("change_articles", { ids: [a.id], read: !a.read });
      else if (e.key === "u") run("undo", {}, "Last change undone");
      else if (e.key === "o" && a)
        window.open(a.url, "_blank", "noopener,noreferrer");
      else if (e.key === "r")
        run(
          "enqueue",
          {
            kind: "refresh",
            payload: scope.feed_id ? { feed_id: scope.feed_id } : {},
          },
          "Refresh queued",
        );
      else if (e.key === "a") setModal("subscribe");
      else if (e.key === "?") setModal("keys");
      else if (e.key === " ") {
        e.preventDefault();
        readingRef.current?.scrollBy({
          top: readingRef.current.clientHeight * 0.85,
          behavior: "smooth",
        });
      } else if (e.key === "Escape") {
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
        history: "Recently read",
      }[view];
  const count = articles.data?.total || 0;
  const folderPath = (id) => {
    const parts = [],
      seen = new Set();
    while (id && !seen.has(id)) {
      seen.add(id);
      const f = data.folders.find((x) => x.id === id);
      if (!f) break;
      parts.unshift(f.name);
      id = f.parent_id;
    }
    return parts.join(" / ");
  };
  const folderTree = (parent = null, depth = 0) =>
    data.folders
      .filter((f) => f.parent_id === parent)
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
              <span className="count">{f.unread || "—"}</span>
            </button>
          </div>
          {!collapsed["nav" + f.id] && (
            <>
              {data.feeds
                .filter((x) => x.folder_id === f.id)
                .map((f) => (
                  <button
                    key={f.id}
                    className={
                      "feed-line " + (scope.feed_id === f.id ? "active" : "")
                    }
                    style={{ paddingLeft: 41 + depth * 14 }}
                    onClick={() => stream("all", { feed_id: f.id })}
                  >
                    <Favicon feed={f} enabled={settings.images} />
                    <span>{f.title}</span>
                    {f.health === "error" && (
                      <AlertCircle size={13} className="error-color" />
                    )}
                    <span className="count">{f.unread || ""}</span>
                  </button>
                ))}
              {folderTree(f.id, depth + 1)}
            </>
          )}
        </React.Fragment>
      ));

  return (
    <main className="shell">
      <aside className="sidebar" aria-label="Library and articles">
        <header className="brand">
          <a href="/" aria-label="Feedelio home">
            <span className="brand-mark">
              <Rss size={20} />
            </span>
            <span>
              feedelio<span className="brand-dot">.</span>
            </span>
          </a>
          <span className="private-label">
            <ShieldCheck size={12} /> JUST FOR YOU
          </span>
        </header>
        <div className="library-controls">
          <div className="search">
            <Search size={16} />
            <input
              ref={searchRef}
              aria-label="Search articles"
              placeholder="Search your library"
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
            {q ? (
              <IconButton
                icon={X}
                label="Clear search"
                onClick={() => setQ("")}
              />
            ) : (
              <kbd>/</kbd>
            )}
          </div>
          <nav className="views" aria-label="Streams">
            {[
              ["all", Inbox, "All articles", data.counts.unread],
              ["starred", Star, "Saved", data.counts.starred],
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
                {n !== null && <span className="pill">{n}</span>}
              </button>
            ))}
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
              YOUR FEEDS <span>{data.feeds.length}</span>
            </button>
            <div>
              <IconButton
                icon={FolderPlus}
                label="Manage folders"
                onClick={() => setModal("folders")}
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
        <div className="list-heading">
          <div>
            <h1>{heading}</h1>
            <span>
              {count.toLocaleString()} {count === 1 ? "article" : "articles"}
            </span>
          </div>
          <div className="list-actions">
            <IconButton
              icon={CheckCheck}
              label="Mark this stream as read"
              onClick={() =>
                run("mark_all", scope, "Stream marked as read · U to undo")
              }
            />
            <IconButton
              icon={RefreshCw}
              label="Refresh feeds (R)"
              onClick={() =>
                run(
                  "enqueue",
                  {
                    kind: "refresh",
                    payload: scope.feed_id ? { feed_id: scope.feed_id } : {},
                  },
                  "Refresh queued",
                )
              }
            />
          </div>
        </div>
        <div className="list-filters">
          <button
            className={settings.hide_read ? "on" : ""}
            onClick={() => pref("hide_read", !settings.hide_read)}
          >
            <span className="toggle-dot" />
            Unread only
          </button>
          <button
            onClick={() =>
              pref("sort", settings.sort === "oldest" ? "newest" : "oldest")
            }
          >
            <ArrowUpDown size={13} />
            {settings.sort === "oldest" ? "Oldest first" : "Newest first"}
          </button>
        </div>
        <div className="article-list" aria-label="Article list">
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
            [...groups].map(([id, group]) => (
              <section key={id} className="article-group">
                <button
                  className="group-heading"
                  onClick={() =>
                    setCollapsed({
                      ...collapsed,
                      ["list" + id]: !collapsed["list" + id],
                    })
                  }
                >
                  {collapsed["list" + id] ? (
                    <ChevronRight size={13} />
                  ) : (
                    <ChevronDown size={13} />
                  )}
                  <Folder size={13} />
                  <span>{folderPath(id)}</span>
                  <span>{group.length}</span>
                </button>
                {!collapsed["list" + id] &&
                  group.map((item) => (
                    <button
                      key={item.id}
                      className={
                        "article-row " +
                        (selected === item.id ? "selected " : "") +
                        (item.read ? "read" : "unread")
                      }
                      onClick={() => open(item)}
                      aria-current={selected === item.id ? "true" : undefined}
                    >
                      <div className="row-meta">
                        <span className="source">{item.feed_title}</span>
                        <span>{date(item.published)}</span>
                      </div>
                      <div className="row-title">
                        <span className="unread-dot" />
                        <h3>{item.title}</h3>
                        {!!item.starred && (
                          <Star
                            size={13}
                            className="starred"
                            fill="currentColor"
                          />
                        )}
                      </div>
                      <p>
                        {item.excerpt || "Open this article to start reading."}
                      </p>
                      <div className="row-bottom">
                        <span>
                          {Math.max(1, Math.ceil(item.words / 230))} min read
                        </span>
                        {item.tags.slice(0, 2).map((t) => (
                          <span className="tag" key={t}>
                            {t}
                          </span>
                        ))}
                        {!!item.duplicate && (
                          <span className="tag">Similar story</span>
                        )}
                      </div>
                    </button>
                  ))}
              </section>
            ))
          )}
          {count > 100 && (
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
        <footer className="sidebar-footer">
          <button onClick={() => setModal("feeds")}>
            <Settings2 size={16} />
            Manage library
          </button>
          <div>
            <span
              className={
                "status-dot " +
                (data.jobs.some((j) => j.status === "running") ? "working" : "")
              }
            />
            <span>
              {busy
                ? "Saving…"
                : data.jobs.some((j) => j.status === "running")
                  ? "Updating"
                  : "Your private library"}
            </span>
            <IconButton
              icon={Keyboard}
              label="Keyboard shortcuts (?)"
              onClick={() => setModal("keys")}
            />
          </div>
        </footer>
      </aside>
      <section className="reader-pane" aria-label="Reading pane">
        <header className="reader-toolbar">
          <div className="breadcrumbs">
            <BookOpen size={16} />
            <span>{a ? a.feed_title : "Your reading space"}</span>
            {a && (
              <>
                <ChevronRight size={13} />
                <span>Article</span>
              </>
            )}
          </div>
          <div>
            <IconButton
              icon={Type}
              label="Reading appearance"
              onClick={() => setModal("appearance")}
            />
            <span className="toolbar-divider" />
            <IconButton
              icon={ChevronLeft}
              label="Previous article (K)"
              disabled={!navigationItems.length}
              onClick={() => navigate(-1)}
            />
            <IconButton
              icon={ChevronRight}
              label="Next article (J)"
              disabled={!navigationItems.length}
              onClick={() => navigate(1)}
            />
          </div>
        </header>
        {!selected ? (
          <div className="welcome">
            <div className="welcome-illustration">
              <div className="orbit orbit-one" />
              <div className="orbit orbit-two" />
              <span className="leaf-one">
                <Leaf size={25} />
              </span>
              <span className="leaf-two">
                <Rss size={19} />
              </span>
              <BookOpen size={52} strokeWidth={1} />
            </div>
            <span className="eyebrow">A LITTLE ROOM TO READ</span>
            <h2>
              Your feeds.
              <br />
              At your own pace.
            </h2>
            <p>
              A quiet home for the ideas, stories, and voices
              <br />
              you want to spend time with.
            </p>
            <button
              className="primary"
              onClick={() =>
                items.length ? open(items[0]) : setModal("subscribe")
              }
            >
              {items.length ? "Start reading" : "Find your first feed"}
              <ArrowUpRight size={16} />
            </button>
            <div className="welcome-shortcuts">
              <span>
                <kbd>J</kbd>
                <kbd>K</kbd> move between articles
              </span>
              <span>
                <kbd>?</kbd> all shortcuts
              </span>
            </div>
            <span className="welcome-footnote">
              <ShieldCheck size={13} />
              Private by design. Yours to keep.
            </span>
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
                    "--reading-size": (settings.font_size || 19) + "px",
                    "--reading-width": (settings.width || 65) + "ch",
                    "--reading-line": settings.line_height || 1.7,
                  }}
                >
                  <div className="article-kicker">
                    <span>{a.feed_title}</span>
                    <span>·</span>
                    <span>{fullDate(a.published)}</span>
                  </div>
                  <h1>{a.title}</h1>
                  <div className="article-byline">
                    {a.author && (
                      <>
                        <span>By {a.author}</span>
                        <span>·</span>
                      </>
                    )}
                    <Clock3 size={13} />
                    <span>{a.reading_minutes} min read</span>
                    <span>·</span>
                    <span>{a.words.toLocaleString()} words</span>
                  </div>
                  <div className="article-tools">
                    <div>
                      <button
                        className={a.starred ? "saved" : ""}
                        onClick={() =>
                          run("change_articles", {
                            ids: [a.id],
                            starred: !a.starred,
                          })
                        }
                      >
                        <Star
                          size={16}
                          fill={a.starred ? "currentColor" : "none"}
                        />
                        {a.starred ? "Saved" : "Save for later"}
                      </button>
                      <button
                        onClick={() =>
                          run("change_articles", { ids: [a.id], read: !a.read })
                        }
                      >
                        {a.read ? (
                          <CheckCircle2 size={16} />
                        ) : (
                          <Circle size={16} />
                        )}{" "}
                        {a.read ? "Read" : "Unread"}
                      </button>
                    </div>
                    <a href={a.url} target="_blank" rel="noreferrer">
                      Original <ArrowUpRight size={15} />
                    </a>
                  </div>
                  {a.embed && (
                    <div className="video-box">
                      {playVideo ? (
                        <iframe
                          title={a.title}
                          src={a.embed}
                          allow="fullscreen; picture-in-picture"
                          allowFullScreen
                          referrerPolicy="no-referrer"
                        />
                      ) : (
                        <button onClick={() => setPlayVideo(true)}>
                          <Play size={30} />
                          <span>Play video</span>
                          <small>
                            {settings.invidious
                              ? "Via your Invidious instance"
                              : "Via youtube-nocookie.com"}{" "}
                            · Loads on click
                          </small>
                        </button>
                      )}
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
                          "Article deleted · U to undo",
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
                  <kbd>J</kbd> next <kbd>K</kbd> previous <kbd>S</kbd> save{" "}
                  <kbd>M</kbd> read / unread
                </span>
                <span>
                  Made for a slower internet <Leaf size={13} />
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
      {modal === "appearance" && (
        <Appearance
          settings={settings}
          pref={pref}
          onClose={() => setModal(null)}
        />
      )}
      {modal === "keys" && (
        <Modal
          title="Make yourself at home"
          subtitle="A few keys. A little less clicking."
          onClose={() => setModal(null)}
        >
          <div className="shortcut-list">
            {[
              ["J / ↓", "Next article"],
              ["K / ↑", "Previous article"],
              ["Space", "Scroll article"],
              ["S", "Save / unsave"],
              ["M", "Read / unread"],
              ["U", "Undo last change"],
              ["/", "Search your library"],
              ["O", "Open original"],
              ["R", "Refresh feeds"],
              ["A", "Add subscription"],
              ["?", "This little guide"],
            ].map(([k, v]) => (
              <div key={k}>
                <span>{v}</span>
                <kbd>{k}</kbd>
              </div>
            ))}
          </div>
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
        "folders",
        "rules",
        "data",
        "statistics",
        "downloads",
      ].includes(modal) && (
        <Library
          initial={modal}
          data={data}
          act={act}
          onClose={() => setModal(null)}
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
      title="Something worth following"
      subtitle="A feed, a website, a YouTube channel. Start with a link."
      onClose={onClose}
    >
      <form className="dialog-body" onSubmit={find}>
        <Field label="Website or feed URL">
          <input
            autoFocus
            type="url"
            placeholder="https://example.com"
            value={url}
            onChange={(e) => {
              setUrl(e.target.value);
              setLinks([]);
            }}
            required
          />
        </Field>
        <div className="form-grid">
          <Field label="Folder">
            <select value={folder} onChange={(e) => setFolder(e.target.value)}>
              {data.folders.map((f) => (
                <option key={f.id} value={f.id}>
                  {f.name}
                </option>
              ))}
            </select>
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
            {links.map((link) => (
              <button
                type="button"
                key={link.url}
                disabled={loading}
                onClick={() => add(link.url)}
              >
                <Rss size={18} />
                <span>
                  <strong>{link.title}</strong>
                  <small>{link.url}</small>
                </span>
                <Plus size={18} />
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
          <button className="primary" disabled={loading || !url}>
            {loading ? (
              <Loader2 size={16} className="spin" />
            ) : (
              <Search size={16} />
            )}
            Find feeds
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

function Appearance({ settings: s, pref, onClose }) {
  return (
    <Modal
      title="Settle into your reading"
      subtitle="Make this space feel like yours."
      onClose={onClose}
    >
      <div className="dialog-body">
        <Field label="Theme">
          <div className="segmented">
            {[
              ["system", Monitor, "System"],
              ["light", Sun, "Light"],
              ["dark", Moon, "Dark"],
            ].map(([v, I, label]) => (
              <button
                key={v}
                className={s.theme === v ? "active" : ""}
                onClick={() => pref("theme", v)}
              >
                <I size={16} />
                {label}
              </button>
            ))}
          </div>
        </Field>
        <Field label="Article typeface">
          <div className="segmented fonts">
            {[
              ["serif", "Georgia"],
              ["sans", "System"],
              ["mono", "Mono"],
            ].map(([v, label]) => (
              <button
                key={v}
                className={"font-" + v + " " + (s.font === v ? "active" : "")}
                onClick={() => pref("font", v)}
              >
                <span>Aa</span>
                {label}
              </button>
            ))}
          </div>
        </Field>
        {[
          ["font_size", "Text size", 16, 32, 1, "px"],
          ["width", "Line length", 45, 75, 1, "ch"],
          ["line_height", "Line spacing", 1.4, 2.2, 0.1, ""],
        ].map(([k, label, min, max, step, unit]) => (
          <Field key={k} label={label + " · " + s[k] + unit}>
            <input
              type="range"
              min={min}
              max={max}
              step={step}
              value={s[k]}
              onChange={(e) => pref(k, Number(e.target.value))}
            />
          </Field>
        ))}
        <label className="check-field">
          <input
            type="checkbox"
            checked={s.images}
            onChange={(e) => pref("images", e.target.checked)}
          />
          <span>
            Load article images and feed icons
            <small>
              Off by default. You can load images for one article at a time.
            </small>
          </span>
        </label>
        <label className="check-field">
          <input
            type="checkbox"
            checked={s.deduplicate}
            onChange={(e) => pref("deduplicate", e.target.checked)}
          />
          <span>
            Hide similar stories across feeds
            <small>Saved stories are always visible.</small>
          </span>
        </label>
        <div
          className={"type-preview font-" + s.font}
          style={{ fontSize: s.font_size, lineHeight: s.line_height }}
        >
          “The more slowly you read, the more the world opens up.”
        </div>
      </div>
    </Modal>
  );
}

function Library({ initial, data, act, onClose, onSubscribe }) {
  const [tab, setTab] = useState(initial),
    [ids, setIds] = useState([]),
    [edit, setEdit] = useState(null),
    [error, setError] = useState("");
  const downloads = data.downloads || [];
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
    <Modal
      wide
      title="Your library, your way"
      subtitle="A little tending makes for better reading."
      onClose={onClose}
    >
      <nav className="dialog-tabs">
        {[
          ["feeds", "Subscriptions"],
          ["folders", "Folders"],
          ["rules", "Rules"],
          ["data", "Data & connections"],
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
      <div className="dialog-body manager-body">
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
                  <span>{data.feeds.length} subscriptions</span>
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
                            { ids, action: "move", folder_id: e.target.value },
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
                            "Refresh queued",
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
                            "Subscriptions deleted · U to undo",
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
                  {data.feeds.map((f) => (
                    <div key={f.id}>
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
                      <button className="feed-info" onClick={() => setEdit(f)}>
                        <strong>{f.title}</strong>
                        <small>{f.url}</small>
                      </button>
                      <span
                        className={"health " + f.health}
                        title={f.error || ""}
                      >
                        {f.health === "healthy"
                          ? "Healthy"
                          : f.health === "quiet"
                            ? "Quiet for 90d"
                            : f.health === "pending"
                              ? "Awaiting fetch"
                              : "Fetch error"}
                      </span>
                      <span className="muted">{f.interval}m</span>
                      <IconButton
                        icon={Settings2}
                        label={"Edit " + f.title}
                        onClick={() => setEdit(f)}
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
          <>
            <form
              className="form-grid"
              onSubmit={(e) => {
                e.preventDefault();
                const form = e.currentTarget,
                  p = new FormData(form);
                submit(async () => {
                  await act(
                    "save_folder",
                    { name: p.get("name"), parent_id: p.get("parent") || null },
                    "Folder created",
                  );
                  form.reset();
                });
              }}
            >
              <Field label="New folder">
                <input name="name" required placeholder="A subject you love" />
              </Field>
              <Field label="Inside">
                <select name="parent">
                  <option value="">Top level</option>
                  {data.folders
                    .filter((f) => f.id !== "inbox")
                    .map((f) => (
                      <option key={f.id} value={f.id}>
                        {f.name}
                      </option>
                    ))}
                </select>
              </Field>
              <button className="primary">
                <FolderPlus size={15} />
                Create folder
              </button>
            </form>
            <hr />
            {data.folders
              .filter((f) => f.id !== "inbox")
              .map((f) => (
                <form
                  className="folder-editor"
                  key={f.id}
                  onSubmit={(e) => {
                    e.preventDefault();
                    const p = new FormData(e.currentTarget);
                    submit(() =>
                      act(
                        "save_folder",
                        {
                          id: f.id,
                          name: p.get("name"),
                          parent_id: p.get("parent") || null,
                        },
                        "Folder saved",
                      ),
                    );
                  }}
                >
                  <Folder size={17} />
                  <input
                    name="name"
                    aria-label="Folder name"
                    defaultValue={f.name}
                  />
                  <select
                    name="parent"
                    aria-label="Parent folder"
                    defaultValue={f.parent_id || ""}
                  >
                    <option value="">Top level</option>
                    {data.folders
                      .filter((x) => x.id !== f.id && x.id !== "inbox")
                      .map((x) => (
                        <option key={x.id} value={x.id}>
                          {x.name}
                        </option>
                      ))}
                  </select>
                  <button>Save</button>
                  <IconButton
                    type="button"
                    icon={Trash2}
                    label={"Delete folder " + f.name}
                    onClick={() =>
                      submit(() =>
                        act(
                          "delete_folder",
                          { id: f.id },
                          "Folder removed; feeds moved to Unfiled",
                        ),
                      )
                    }
                  />
                </form>
              ))}
            <p className="muted">
              Every feed belongs to one folder. Deleting a folder moves its
              feeds to Unfiled and its children up one level.
            </p>
          </>
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
                  onClick={() => submit(() => act("delete_rule", { id: r.id }))}
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
                <input name="name" required placeholder="Keep the good stuff" />
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
              contacting the publisher. Open an episode to add it to the queue.
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
                    <strong>{Math.round(stats.data.totals.words / 230)}</strong>
                    <span>Estimated reading minutes</span>
                  </div>
                  <div>
                    <strong>{stats.data.totals.saved}</strong>
                    <span>Saved for later</span>
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
                  Reading time is estimated at 230 words per minute. Opening an
                  article is counted separately from marking a stream as read.
                </p>
              </>
            ) : (
              <p>Loading activity…</p>
            )}
          </>
        )}
      </div>
    </Modal>
  );
}

function FeedEditor({ feed: f, data, act, onBack }) {
  const [error, setError] = useState("");
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
      <h3>{f.title}</h3>
      {f.error && <p className="inline-error">{f.error}</p>}
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
          <select name="folder_id" defaultValue={f.folder_id}>
            {data.folders.map((x) => (
              <option key={x.id} value={x.id}>
                {x.name}
              </option>
            ))}
          </select>
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
          name="archive"
          defaultChecked={f.options.archive}
        />
        <span>Keep this feed’s archive forever</span>
      </label>
      <label className="check-field">
        <input
          type="checkbox"
          name="extract"
          defaultChecked={f.options.extract}
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
        uses the saved archive URL and follows available next-page links.
      </p>
    </form>
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
          ? result.imported + " subscriptions imported."
          : result.restored + " articles restored.",
      );
    });
    e.target.value = "";
  }
  return (
    <>
      <div className="data-card">
        <div>
          <h3>Bring your reading with you</h3>
          <p>
            Import folders and subscriptions from Inoreader or another reader.
          </p>
        </div>
        <label className="file-button">
          <Plus size={15} />
          Import OPML
          <input
            type="file"
            accept=".opml,.xml"
            onChange={(e) => importFile(e, "opml")}
          />
        </label>
        <a className="button" href="/api/export/opml" download>
          <Download size={15} />
          Export OPML
        </a>
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
      {notice && <p role="status">{notice}</p>}
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
                },
              },
              "Library settings saved",
            ),
          );
        }}
      >
        <Field
          label="Retain read articles for (days)"
          hint="0 keeps everything. Unread, saved and archive-feed articles are protected."
        >
          <input
            name="retention_days"
            type="number"
            min="0"
            max="36500"
            defaultValue={data.settings.retention_days}
          />
        </Field>
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
    </>
  );
}

createRoot(document.getElementById("root")).render(
  <QueryClientProvider client={queryClient}>
    <App />
  </QueryClientProvider>,
);
