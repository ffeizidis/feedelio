import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useQueryClient } from "@tanstack/react-query";

export const feedClass = (feed) => `feed-hue-${feed?.options?.hue ?? 0}`;

// Every form here runs one action and shows what went wrong in place.
export function useSubmit() {
  const [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  async function submit(fn) {
    setError("");
    setBusy(true);
    try {
      return (await fn()) ?? true; // Never undefined, so callers can test success.
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  return { error, setError, busy, submit };
}

export function ResizeHandle({
  label,
  value,
  min,
  max,
  start = 0,
  onChange,
  onSave,
}) {
  const drag = useRef(null);
  const clamp = (n) => Math.round(Math.max(min, Math.min(max, n)));
  return (
    <div
      className="pane-divider"
      role="separator"
      tabIndex={0}
      aria-label={label}
      aria-orientation="vertical"
      aria-valuemin={min}
      aria-valuemax={max}
      aria-valuenow={Math.round(value)}
      onPointerDown={(e) => {
        if (e.button !== 0) return;
        drag.current = value;
        e.currentTarget.setPointerCapture(e.pointerId);
        e.preventDefault();
      }}
      onPointerMove={(e) => {
        if (drag.current !== null) onChange(clamp(e.clientX - start));
      }}
      onPointerUp={(e) => {
        if (drag.current === null) return;
        drag.current = null;
        onSave(clamp(e.clientX - start));
      }}
      onPointerCancel={() => {
        if (drag.current !== null) onChange(drag.current);
        drag.current = null;
      }}
      onKeyDown={(e) => {
        if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) return;
        e.preventDefault();
        e.stopPropagation();
        const n = clamp(
          e.key === "Home"
            ? min
            : e.key === "End"
              ? max
              : value + (e.key === "ArrowLeft" ? -20 : 20),
        );
        onChange(n);
        onSave(n);
      }}
    />
  );
}

export function Switch({ label, checked, onChange }) {
  return (
    <button
      type="button"
      className="switch-control"
      role="switch"
      aria-label={label}
      aria-checked={checked}
      onClick={() => onChange(!checked)}
    >
      <span className="switch-track" />
      <span>{label}</span>
    </button>
  );
}

export function Highlight({ text = "", query }) {
  const terms = query?.match(/[\p{L}\p{N}_]+/gu) || [];
  if (!terms.length) return text;
  const pattern = new RegExp(`(${terms.join("|")})`, "giu");
  return String(text)
    .split(pattern)
    .map((part, i) => (i % 2 ? <mark key={i}>{part}</mark> : part));
}

export const shortcutGroups = {
  Navigate: [
    ["j / k", "next / prev article"],
    ["n / p", "next / prev feed"],
    ["g then a", "All articles"],
    ["g then s", "Saved"],
    ["/", "Search"],
  ],
  Act: [
    ["m", "mark read / unread"],
    ["s", "star"],
    ["↵", "open in reader"],
    ["⇧↵", "open original"],
    ["⇧A", "mark all read"],
    ["z", "undo last change"],
  ],
  View: [
    ["u", "unread only"],
    ["[ / ]", "narrow / widen list"],
    ["1 2 3", "list mode"],
    [",", "Manage library"],
    ["?", "this sheet"],
  ],
};
export function KeyboardGuide() {
  return (
    <div className="keyboard-columns">
      {Object.entries(shortcutGroups).map(([label, rows]) => (
        <section key={label}>
          <h3 className="section-label">{label}</h3>
          {rows.map(([key, text]) => (
            <div className="key-row" key={key}>
              <kbd>{key}</kbd>
              <span>{text}</span>
            </div>
          ))}
        </section>
      ))}
    </div>
  );
}

function Choices({ label, value, options, onChange }) {
  return (
    <div className="choice-pills" role="group" aria-label={label}>
      {options.map(([id, text]) => (
        <button
          type="button"
          key={id}
          aria-pressed={value === id}
          className={value === id ? "active" : ""}
          onClick={() => onChange(id)}
        >
          {text}
        </button>
      ))}
    </div>
  );
}
function Setting({ label, note, children }) {
  return (
    <div className="appearance-row">
      <span>{label}</span>
      <div>
        {children}
        {note && <small>{note}</small>}
      </div>
    </div>
  );
}
export function AppearanceSettings({ settings: s, pref }) {
  return (
    <div className="appearance-settings">
      <section>
        <h3 className="section-label">Theme & colour</h3>
        <Setting label="Theme">
          <Choices
            label="Theme"
            value={s.theme}
            options={[
              ["dark", "Dark"],
              ["light", "Light"],
              ["system", "Match system"],
            ]}
            onChange={(v) => pref("theme", v)}
          />
        </Setting>
        <Setting
          label="Accent"
          note="Used for the wordmark, links, selection and the one filled button."
        >
          <div className="swatches">
            {["blue", "violet", "green", "amber", "red"].map((c) => (
              <button
                key={c}
                aria-label={`${c} accent`}
                aria-pressed={s.accent === c}
                className={`swatch swatch-${c}`}
                onClick={() => pref("accent", c)}
              />
            ))}
          </div>
        </Setting>
        <Setting
          label="Feed colours"
          note="Each feed keeps a hue; rows, chips and the reader inherit it. Off makes every feed the accent."
        >
          <Switch
            label="Feed colours"
            checked={s.feed_colours}
            onChange={(v) => pref("feed_colours", v)}
          />
        </Setting>
        <Setting label="Unread marker">
          <Choices
            label="Unread marker"
            value={s.unread_marker}
            options={[
              ["count", "Count"],
              ["dot", "Dot"],
              ["none", "None"],
            ]}
            onChange={(v) => pref("unread_marker", v)}
          />
        </Setting>
        <Setting label="Wordmark">
          <Choices
            label="Wordmark"
            value={s.wordmark}
            options={[
              ["script", <span className="wordmark-sample">feedelio</span>],
              ["plain", "Plain"],
            ]}
            onChange={(v) => pref("wordmark", v)}
          />
        </Setting>
        <Setting
          label="Reduce motion"
          note="Hover and pane-drag transitions only; there is no other motion."
        >
          <Switch
            label="Reduce motion"
            checked={s.reduce_motion}
            onChange={(v) => pref("reduce_motion", v)}
          />
        </Setting>
      </section>
      <section>
        <h3 className="section-label">List mode</h3>
        <div
          className="list-mode-options"
          role="group"
          aria-label="Article list display"
        >
          {[
            ["title", "Titles only", "38px rows"],
            ["preview", "Title + snippet", "two lines"],
            ["compact", "Compact", "28px rows, no feed name"],
          ].map(([id, label, spec]) => (
            <button
              key={id}
              className={`mode-option ${s.list_display === id ? "active" : ""}`}
              aria-pressed={s.list_display === id}
              onClick={() => pref("list_display", id)}
            >
              <span className="mode-label">
                <span className="radio-dot" />
                {label}
                <small>{spec}</small>
              </span>
              <span className={`mode-preview ${id}`}>
                <span>
                  <i className="hue-dot" />
                  <em>Journal</em>
                  <span>Utopia is a particular restaurant</span>
                  <time>Jun 1</time>
                </span>
                {id === "preview" ? (
                  <span className="sample-snippet">
                    Eleven tables, one chef, no ambition beyond dinner…
                  </span>
                ) : (
                  <span className="feed-hue-1">
                    <i className="hue-dot" />
                    <em>Craft & Method</em>
                    <span>Notes on making things slowly</span>
                    <time>May 22</time>
                  </span>
                )}
              </span>
            </button>
          ))}
        </div>
      </section>
      <section>
        <h3 className="section-label">Reading</h3>
        <Setting label="Body face">
          <Choices
            label="Article typeface"
            value={s.font}
            options={[
              ["serif", <span className="font-serif">Georgia</span>],
              [
                "newsreader",
                <span className="font-newsreader">Newsreader</span>,
              ],
              ["sans", "System"],
            ]}
            onChange={(v) => pref("font", v)}
          />
        </Setting>
        <Setting label="Text size">
          <div className="size-stepper">
            <button
              aria-label="Decrease text size"
              disabled={s.font_size <= 16}
              onClick={() => pref("font_size", s.font_size - 1)}
            >
              −
            </button>
            <output>{s.font_size}px</output>
            <button
              aria-label="Increase text size"
              disabled={s.font_size >= 32}
              onClick={() => pref("font_size", s.font_size + 1)}
            >
              +
            </button>
          </div>
        </Setting>
        <Setting label="Line width">
          <Choices
            label="Line width"
            value={s.width}
            options={[
              [45, "Narrow"],
              [65, "Comfortable"],
              [75, "Wide"],
            ]}
            onChange={(v) => pref("width", v)}
          />
        </Setting>
        <Setting label="Line spacing">
          <input
            aria-label="Line spacing"
            type="range"
            min="1.4"
            max="2.2"
            step="0.1"
            value={s.line_height}
            onChange={(e) => pref("line_height", +e.target.value)}
          />
        </Setting>
        <Setting label="Open titles in">
          <Choices
            label="Open titles in"
            value={s.open_titles_in}
            options={[
              ["reader", "Reader"],
              ["original", "Original page"],
            ]}
            onChange={(v) => pref("open_titles_in", v)}
          />
        </Setting>
        <Setting
          label="Images"
          note="Off saves data. Images can also be loaded for one article at a time."
        >
          <Switch
            label="Load article images and feed icons"
            checked={s.images}
            onChange={(v) => pref("images", v)}
          />
        </Setting>
        <Setting label="Similar stories">
          <Switch
            label="Hide similar stories across feeds"
            checked={s.deduplicate}
            onChange={(v) => pref("deduplicate", v)}
          />
        </Setting>
      </section>
    </div>
  );
}

// Every dropdown in the app: a button that opens a listbox which stays open
// until an option is picked, Escape, or a click elsewhere. Native <select>
// popups close on mouseup in some browsers and cannot indent nested folders.
// Below this many options a filter field is noise rather than help.
const SEARCH_FROM = 8;

export function Select({
  label,
  name,
  value,
  defaultValue = "",
  options,
  onChange,
  placeholder,
}) {
  const [inner, setInner] = useState(defaultValue),
    [open, setOpen] = useState(false),
    [cursor, setCursor] = useState(0),
    [place, setPlace] = useState({}),
    [query, setQuery] = useState(""),
    root = useRef(null),
    search = useRef(null),
    list = useRef(null);
  const current = value === undefined ? inner : value,
    chosen = options.find((o) => o.value === current);
  // A handful of choices are quicker to read than to filter; a list of feeds or
  // folders is not, so past a threshold the menu can be narrowed by typing.
  const searchable = options.length > SEARCH_FROM;
  const shown = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!searchable || !needle) return options;
    return options.filter((o) => o.label.toLowerCase().includes(needle));
  }, [options, query, searchable]);
  function pick(option) {
    setOpen(false);
    if (!option || option.disabled) return;
    setInner(option.value);
    onChange?.(option.value);
  }
  // Fixed position so the menu escapes dialogs and scroll panes; it is
  // recomputed while the page moves rather than closing, because a dropdown
  // must stay open until an option is picked.
  const locate = useCallback(() => {
    const box = root.current.getBoundingClientRect(),
      below = innerHeight - box.bottom - 12,
      above = box.top - 12,
      up = below < 200 && above > below;
    setPlace({
      left: box.left,
      minWidth: box.width,
      maxWidth: innerWidth - box.left - 12,
      maxHeight: Math.min(360, Math.max(120, up ? above : below)),
      ...(up ? { bottom: innerHeight - box.top + 4 } : { top: box.bottom + 4 }),
    });
  }, []);
  function show() {
    locate();
    setQuery("");
    setCursor(Math.max(0, options.indexOf(chosen)));
    setOpen(true);
  }
  // Keeping the cursor in view scrolls the menu itself; scrollIntoView would
  // scroll the pane behind it as well and drag the menu away from its button.
  function reveal(index) {
    const menu = list.current,
      item = menu?.children[index];
    if (!item) return;
    if (item.offsetTop < menu.scrollTop) menu.scrollTop = item.offsetTop;
    else if (
      item.offsetTop + item.offsetHeight >
      menu.scrollTop + menu.clientHeight
    )
      menu.scrollTop = item.offsetTop + item.offsetHeight - menu.clientHeight;
  }
  useEffect(() => {
    if (!open) return;
    const away = (e) => root.current?.contains(e.target) || setOpen(false),
      move = (e) => (list.current?.contains(e.target) ? null : locate());
    document.addEventListener("pointerdown", away);
    addEventListener("resize", locate);
    addEventListener("scroll", move, true);
    reveal(cursor);
    return () => {
      document.removeEventListener("pointerdown", away);
      removeEventListener("resize", locate);
      removeEventListener("scroll", move, true);
    };
  }, [open]);
  useEffect(() => {
    if (open) reveal(cursor);
  }, [cursor]);
  useEffect(() => {
    if (open && searchable) search.current?.focus();
  }, [open, searchable]);
  // Narrowing the list moves the cursor back to the top of what is left.
  useEffect(() => setCursor(0), [query]);
  function key(e) {
    const step = { ArrowDown: 1, ArrowUp: -1 }[e.key];
    if (!open) {
      if (step || e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        show();
      }
      return;
    }
    e.stopPropagation();
    const typing = e.target === search.current;
    if (step) {
      e.preventDefault();
      setCursor((c) => Math.min(shown.length - 1, Math.max(0, c + step)));
    } else if ((e.key === "Home" || e.key === "End") && !typing) {
      e.preventDefault();
      setCursor(e.key === "Home" ? 0 : shown.length - 1);
    } else if (e.key === "Enter" || (e.key === " " && !typing)) {
      // A space belongs to the query while the filter has focus.
      e.preventDefault();
      pick(shown[cursor]);
    } else if (e.key === "Escape" || e.key === "Tab") {
      if (e.key === "Escape") e.preventDefault();
      setOpen(false);
    }
  }
  return (
    <div className="select" ref={root} onKeyDown={key}>
      {name && <input type="hidden" name={name} value={current} />}
      <button
        type="button"
        role="combobox"
        aria-label={label}
        aria-haspopup="listbox"
        aria-expanded={open}
        className={chosen ? "" : "placeholder"}
        onClick={() => (open ? setOpen(false) : show())}
      >
        <span>{chosen?.label ?? placeholder ?? ""}</span>
        <svg width="14" height="14" viewBox="0 0 24 24" aria-hidden="true">
          <path
            d="m6 9 6 6 6-6"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </button>
      {open && (
        <div className="select-menu" style={place}>
          {searchable && (
            <input
              ref={search}
              className="select-search"
              type="text"
              autoComplete="off"
              spellCheck={false}
              aria-label={`Search ${label}`}
              placeholder="Type to search…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          )}
          <ul
            role="listbox"
            aria-label={label}
            className="select-list"
            ref={list}
          >
            {shown.map((o, i) => (
              <li
                key={o.value}
                role="option"
                aria-selected={o.value === current}
                aria-disabled={o.disabled || undefined}
                className={i === cursor ? "focused" : ""}
                style={{ paddingLeft: 12 + (o.depth || 0) * 16 }}
                onPointerMove={() => setCursor(i)}
                onClick={(e) => {
                  e.preventDefault(); // a wrapping <label> would re-click the button
                  pick(o);
                }}
              >
                {o.label}
              </li>
            ))}
          </ul>
          {shown.length === 0 && <p className="select-empty">No matches</p>}
        </div>
      )}
    </div>
  );
}

// Folders as dropdown options in sidebar order: depth-first by name, with
// Unfiled last among the top-level ones; excluded folders take their subtrees.
export function folderOptions(
  folders,
  exclude = new Set(),
  parent = null,
  depth = 0,
) {
  return folders
    .filter((f) => f.parent_id === parent && !exclude.has(f.id))
    .sort((a, b) => (a.id === "inbox") - (b.id === "inbox"))
    .flatMap((f) => [
      { value: f.id, label: f.name, depth },
      ...folderOptions(folders, exclude, f.id, depth + 1),
    ]);
}

export function FolderSelect({
  data,
  act,
  name,
  label,
  value,
  onChange,
  exclude = new Set(),
  top,
}) {
  const [creating, setCreating] = useState(false),
    [draft, setDraft] = useState(""),
    { error, submit } = useSubmit();
  async function create() {
    if (!draft.trim()) return;
    const created = await submit(() =>
      act(
        "save_folder",
        { name: draft.trim(), parent_id: null },
        "Folder created",
      ),
    );
    if (!created) return;
    onChange(created.id);
    setCreating(false);
    setDraft("");
  }
  return (
    <>
      <Select
        name={name}
        label={label}
        value={value}
        options={[
          ...(top ? [{ value: "", label: "Top level" }] : []),
          ...folderOptions(data.folders, exclude),
          { value: "+", label: "+ New folder…" },
        ]}
        onChange={(v) => (v === "+" ? setCreating(true) : onChange(v))}
      />
      {creating && (
        <div className="inline-folder">
          <input
            autoFocus
            aria-label="New folder name"
            placeholder="New folder name"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                create();
              }
              if (e.key === "Escape") setCreating(false);
            }}
          />
          <button type="button" className="primary" onClick={create}>
            Create
          </button>
          <button type="button" onClick={() => setCreating(false)}>
            Cancel
          </button>
          {error && <p role="alert">{error}</p>}
        </div>
      )}
    </>
  );
}

export function FolderSheet({ data, act, onClose, folder, Modal, Field }) {
  const { error, submit } = useSubmit(),
    [parent, setParent] = useState(folder?.parent_id || "");
  const descendants = new Set(folder ? [folder.id] : []);
  for (let i = 0; i < data.folders.length; i++)
    for (const f of data.folders)
      if (descendants.has(f.parent_id)) descendants.add(f.id);
  return (
    <Modal title={folder ? "Edit folder" : "New folder"} onClose={onClose}>
      <form
        className="dialog-body"
        onSubmit={async (e) => {
          e.preventDefault();
          const values = new FormData(e.currentTarget);
          if (
            await submit(() =>
              act(
                "save_folder",
                {
                  ...(folder ? { id: folder.id } : {}),
                  name: values.get("name"),
                  parent_id: values.get("parent") || null,
                },
                "Folder saved",
              ),
            )
          )
            onClose();
        }}
      >
        <Field label="Folder name">
          <input
            autoFocus
            required
            name="name"
            defaultValue={folder?.name || ""}
          />
        </Field>
        <Field label="Put folder under">
          <FolderSelect
            data={data}
            act={act}
            name="parent"
            label="Put folder under"
            value={parent}
            onChange={setParent}
            exclude={new Set(["inbox", ...descendants])}
            top
          />
        </Field>
        {error && <p role="alert">{error}</p>}
        <div className="sheet-actions">
          {folder && (
            <button
              type="button"
              className="error-color"
              onClick={async () => {
                if (
                  !confirm(
                    `Delete ${folder.name}? Its feeds will move to Unfiled.`,
                  )
                )
                  return;
                if (await submit(() => act("delete_folder", { id: folder.id })))
                  onClose();
              }}
            >
              Delete folder
            </button>
          )}
          <button type="button" onClick={onClose}>
            Cancel
          </button>
          <button className="primary">
            {folder ? "Save folder" : "Create folder"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

export function FolderManager({ data, act, onEdit, onNew }) {
  const [opened, setOpened] = useState({}),
    [dragged, setDragged] = useState(null),
    { error, submit } = useSubmit();
  async function move(feed, folder) {
    if (
      await submit(() =>
        act(
          "bulk_feeds",
          { ids: [feed], action: "move", folder_id: folder },
          "Feed moved",
        ),
      )
    )
      setDragged(null);
  }
  function nest(id, parent) {
    const folder = data.folders.find((f) => f.id === id);
    if (!folder || folder.parent_id === parent) return;
    submit(() =>
      act(
        "save_folder",
        { id, name: folder.name, parent_id: parent },
        parent ? "Folder nested" : "Folder moved to top level",
      ),
    );
  }
  function drop(e, target) {
    e.preventDefault();
    e.stopPropagation();
    const feed = e.dataTransfer.getData("text/feedelio-feed"),
      folder = e.dataTransfer.getData("text/feedelio-folder");
    if (data.feeds.some((x) => x.id === feed)) move(feed, target || "inbox");
    else if (folder && folder !== target) nest(folder, target);
  }
  const tree = (parent = null) =>
    data.folders
      .filter((f) => f.parent_id === parent)
      .sort((a, b) => (a.id === "inbox") - (b.id === "inbox"))
      .map((f) => {
        const feeds = data.feeds.filter((x) => x.folder_id === f.id);
        return (
          <section
            className="folder-group"
            key={f.id}
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => drop(e, f.id)}
          >
            <div
              className="folder-group-heading"
              draggable={f.id !== "inbox"}
              onDragStart={(e) => {
                e.dataTransfer.setData("text/feedelio-folder", f.id);
                e.stopPropagation();
              }}
            >
              <button
                aria-label={`${opened[f.id] ? "Collapse" : "Expand"} ${f.name}`}
                onClick={() => setOpened({ ...opened, [f.id]: !opened[f.id] })}
              >
                {opened[f.id] ? "▾" : "▸"}
              </button>
              <span>{f.name}</span>
              <small>
                {feeds.length
                  ? `${feeds.length} feeds · ${f.unread} unread`
                  : "empty — drag feeds or folders here"}
              </small>
              {f.id !== "inbox" && (
                <button
                  aria-label={`Edit folder ${f.name}`}
                  onClick={() => onEdit(f)}
                >
                  ⋯
                </button>
              )}
            </div>
            {opened[f.id] && (
              <>
                <div className="folder-nested">{tree(f.id)}</div>
                {feeds.map((feed) => (
                  <div
                    draggable
                    onDragStart={(e) => {
                      e.dataTransfer.setData("text/feedelio-feed", feed.id);
                      setDragged(feed.id);
                    }}
                    onDragEnd={() => setDragged(null)}
                    className={`folder-child ${feedClass(feed)}`}
                    key={feed.id}
                  >
                    <i className="hue-dot" />
                    <span>{feed.title}</span>
                    <small>{feed.unread}</small>
                    <button
                      aria-label={`Move ${feed.title}`}
                      onClick={() =>
                        setDragged(dragged === feed.id ? null : feed.id)
                      }
                    >
                      ⣿
                    </button>
                    {dragged === feed.id && (
                      <Select
                        label={`Destination for ${feed.title}`}
                        value={feed.folder_id}
                        options={folderOptions(data.folders)}
                        onChange={(v) => move(feed.id, v)}
                      />
                    )}
                  </div>
                ))}
              </>
            )}
          </section>
        );
      });
  return (
    <>
      <div className="manager-heading">
        <span>
          {data.folders.length - 1} folders · {data.feeds.length} feeds ·{" "}
          {data.feeds.filter((f) => f.folder_id === "inbox").length} unfiled
        </span>
        <button className="button" onClick={onNew}>
          + New folder
        </button>
      </div>
      {error && <p role="alert">{error}</p>}
      <div
        className="folder-manager"
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => drop(e, null)}
      >
        {tree()}
        <p className="folder-drop-hint">
          Drop a folder here to move it to the top level.
        </p>
      </div>
    </>
  );
}

const RULE_FIELDS = ["title", "body", "url", "author", "tags"];
const RULE_ACTIONS = ["read", "star", "tag", "drop", "rewrite"];
const BLANK = {
  name: "",
  field: "title",
  pattern: "",
  action: "read",
  value: "",
  feed_id: "",
  is_regex: true,
};

// The add/edit sheet. A Modal rather than fields revealed in place, so closing it
// unmounts the dropdowns and the next rule genuinely starts from the defaults.
function RuleSheet({ data, act, api, rule, onClose, Modal, Field }) {
  const { error, submit, busy } = useSubmit(),
    [form, setForm] = useState({ ...BLANK, ...(rule || {}) }),
    [check, setCheck] = useState(null),
    [tried, setTried] = useState(null),
    [trying, setTrying] = useState(false),
    [triedError, setTriedError] = useState(""),
    request = useRef(0);
  const set = (key) => (v) => setForm((f) => ({ ...f, [key]: v }));
  // Checked by the same engine that runs the rules, so this cannot pass what the
  // server would reject. Debounced, and stale replies are dropped.
  useEffect(() => {
    if (!form.pattern.trim()) return setCheck(null);
    const ticket = ++request.current;
    const timer = setTimeout(() => {
      api("rules/validate", {
        payload: {
          pattern: form.pattern,
          is_regex: !!form.is_regex,
          action: form.action,
          value: form.value,
          field: form.field,
        },
      })
        .then((r) => ticket === request.current && setCheck(r))
        .catch(() => {});
    }, 300);
    return () => clearTimeout(timer);
  }, [form.pattern, form.is_regex, form.action, form.value, form.field]);
  const broken = (check?.errors || []).filter((e) => e.error),
    slow = (check?.errors || []).filter((e) => e.slow),
    blocked = busy || !form.pattern.trim() || broken.length > 0;
  // A count belongs to the rule it was counted for, so editing retires it.
  useEffect(() => {
    setTried(null);
    setTriedError("");
  }, [
    form.pattern,
    form.is_regex,
    form.field,
    form.action,
    form.value,
    form.feed_id,
  ]);
  // The same pass that applying uses, over one rule, saved or not.
  async function tryRule() {
    setTrying(true);
    setTriedError("");
    try {
      setTried(
        await api("rules/preview", {
          payload: { rule: { ...form, feed_id: form.feed_id || null } },
        }),
      );
    } catch (e) {
      setTriedError(e.message);
    } finally {
      setTrying(false);
    }
  }
  return (
    <Modal title={rule ? "Edit rule" : "New rule"} onClose={onClose}>
      <form
        className="dialog-body rule-form"
        onSubmit={async (e) => {
          e.preventDefault();
          const saved = await submit(() =>
            act(
              "save_rule",
              { ...form, feed_id: form.feed_id || null },
              rule ? "Rule saved" : "Rule added",
              { invalidate: [["rules"]] },
            ),
          );
          if (saved) onClose();
        }}
      >
        <Field label="Rule name">
          <input
            name="name"
            required
            value={form.name}
            onChange={(e) => set("name")(e.target.value)}
            placeholder="Keep the good stuff"
          />
        </Field>
        <div className="form-grid">
          <Field label="Match field">
            <Select
              label="Match field"
              value={form.field}
              onChange={set("field")}
              options={RULE_FIELDS.map((x) => ({ value: x, label: x }))}
            />
          </Field>
          <Field label="Action">
            <Select
              label="Action"
              value={form.action}
              onChange={set("action")}
              options={RULE_ACTIONS.map((x) => ({ value: x, label: x }))}
            />
          </Field>
        </div>
        <Field
          label={form.is_regex ? "Patterns (regex)" : "Patterns (plain text)"}
          hint={
            form.is_regex
              ? "One per line — the rule runs if any line matches."
              : "One per line, matched literally, ignoring case."
          }
        >
          <textarea
            name="pattern"
            rows={3}
            required
            spellCheck={false}
            value={form.pattern}
            onChange={(e) => set("pattern")(e.target.value)}
            placeholder="subscribe to continue"
          />
        </Field>
        <Switch
          label="Regular expression"
          checked={!!form.is_regex}
          onChange={set("is_regex")}
        />
        {broken.map((e) => (
          <p className="rule-line-error" key={e.line} role="alert">
            Line {e.line}: {e.error}
          </p>
        ))}
        {slow.map((e) => (
          <p className="rule-line-warning" key={e.line}>
            Line {e.line} is very slow to match and may time out.
          </p>
        ))}
        {check?.value_error && (
          <p className="rule-line-error" role="alert">
            {check.value_error}
          </p>
        )}
        {(form.action === "tag" || form.action === "rewrite") && (
          <Field
            label={form.action === "tag" ? "Tag to add" : "Replacement"}
            hint={
              form.action === "rewrite"
                ? "Substituted into the article body for every matching line."
                : undefined
            }
          >
            <input
              name="value"
              value={form.value}
              onChange={(e) => set("value")(e.target.value)}
            />
          </Field>
        )}
        <Field label="Scope">
          <Select
            label="Scope"
            value={form.feed_id || ""}
            onChange={set("feed_id")}
            options={[
              { value: "", label: "All feeds" },
              ...data.feeds.map((f) => ({ value: f.id, label: f.title })),
            ]}
          />
        </Field>
        {error && (
          <p role="alert" className="inline-error">
            {error}
          </p>
        )}
        {tried && (
          <div className="rule-preview">
            <p>
              This rule would change{" "}
              <strong>{tried.changed.toLocaleString()}</strong> of{" "}
              {tried.processed.toLocaleString()} stored articles.
            </p>
            {tried.changed === 0 && tried.matched > 0 && (
              <p>
                It matches {tried.matched.toLocaleString()} of them, which are
                already in that state — there is nothing left for it to do here.
                It still applies to new articles.
              </p>
            )}
            {tried.matched === 0 && (
              <p className="rule-line-warning">
                Nothing stored matches it. Check the pattern for stray spaces.
              </p>
            )}
            {tried.timeouts > 0 && (
              <p className="rule-line-warning">
                {tried.timeouts.toLocaleString()} article
                {tried.timeouts === 1 ? "" : "s"} took too long to match and
                would be skipped. Simplify the pattern.
              </p>
            )}
          </div>
        )}
        {triedError && (
          <p role="alert" className="inline-error">
            {triedError}
          </p>
        )}
        <div className="form-actions">
          <button
            type="button"
            onClick={tryRule}
            disabled={blocked || trying}
            title="Count what this rule would change, without changing anything"
          >
            {trying ? "Counting…" : "Preview this rule"}
          </button>
          <div className="action-pair">
            <button type="button" onClick={onClose}>
              Cancel
            </button>
            <button className="primary" disabled={blocked}>
              {rule ? "Save rule" : "Add rule"}
            </button>
          </div>
        </div>
      </form>
    </Modal>
  );
}

// What applying the stored rules would do, counted exactly, before it is done.
function RulePreview({ api, act }) {
  const { error, submit, busy } = useSubmit(),
    cache = useQueryClient(),
    [seen, setSeen] = useState(null);
  const total = seen
    ? Object.entries(seen.actions).filter(([, n]) => n > 0)
    : [];
  return (
    <div className="rule-apply">
      <div className="form-actions">
        <button
          type="button"
          disabled={busy}
          onClick={() =>
            submit(async () =>
              setSeen(await api("rules/preview", { payload: {} })),
            )
          }
        >
          {busy ? "Counting…" : "Preview changes"}
        </button>
        {seen && seen.changed > 0 && (
          <button
            className="primary"
            type="button"
            onClick={() =>
              submit(async () => {
                await act(
                  "enqueue",
                  { kind: "rules" },
                  "Applying rules to stored articles",
                  { invalidate: [["overview"]] },
                );
                setSeen(null);
                cache.removeQueries({ queryKey: ["rules", "preview"] });
              })
            }
          >
            Apply these changes
          </button>
        )}
      </div>
      {error && (
        <p role="alert" className="inline-error">
          {error}
        </p>
      )}
      {seen && (
        <div className="rule-preview">
          <p>
            <strong>{seen.changed.toLocaleString()}</strong> of{" "}
            {seen.processed.toLocaleString()} stored articles would change.
          </p>
          {total.length > 0 && (
            <ul>
              {total.map(([action, n]) => (
                <li key={action}>
                  {n.toLocaleString()} {action}
                </li>
              ))}
            </ul>
          )}
          {seen.rules.some((r) => r.changed > 0) && (
            <table className="rule-preview-table">
              <tbody>
                {seen.rules
                  .filter((r) => r.changed > 0)
                  .map((r) => (
                    <tr key={r.id}>
                      <td>{r.name}</td>
                      <td>{r.changed.toLocaleString()}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          )}
          {seen.timeouts > 0 && (
            <p className="rule-line-warning">
              {seen.timeouts.toLocaleString()} article
              {seen.timeouts === 1 ? "" : "s"} took too long to match and would
              be skipped. Simplify the pattern.
            </p>
          )}
          {seen.actions.rewrite > 0 && (
            <p className="rule-line-warning">
              Rewritten article bodies cannot be restored. Back up first.
            </p>
          )}
        </div>
      )}
    </div>
  );
}

export function RulesManager({
  data,
  rules,
  act,
  api,
  Modal,
  Field,
  IconButton,
  Pencil,
  Trash2,
}) {
  const { error, submit } = useSubmit(),
    cache = useQueryClient(),
    [sheet, setSheet] = useState(null);
  // The row moves now; the scoped refetch behind it only confirms. Kept local
  // rather than generalised, since only the rules list is edited this directly.
  const patch = (fn) =>
    cache.setQueryData(["rules"], (rs) => (rs || []).map(fn));
  const preset = (rule) =>
    submit(() =>
      act("save_rule", rule, "Rule added", { invalidate: [["rules"]] }),
    );
  return (
    <>
      <p className="muted">
        Rules run on new articles and extracted content, in the order below.
      </p>
      <div className="rule-presets">
        <button
          onClick={() =>
            preset({
              id: "shorts",
              name: "Skip YouTube Shorts",
              field: "url",
              pattern: "youtube\\.com/shorts/",
              action: "read",
            })
          }
        >
          + Skip YouTube Shorts
        </button>
        <button
          onClick={() =>
            preset({
              id: "paywall",
              name: "Skip paywalled articles",
              field: "tags",
              pattern: "paywall",
              action: "read",
            })
          }
        >
          + Mark paywalls as read
        </button>
        <button className="primary" onClick={() => setSheet({})}>
          + Add new rule
        </button>
      </div>
      {error && (
        <p role="alert" className="inline-error">
          {error}
        </p>
      )}
      {rules.data?.map((r) => (
        <div className="rule-row" key={r.id}>
          <input
            aria-label={"Enable " + r.name}
            type="checkbox"
            checked={!!r.enabled}
            onChange={(e) => {
              const enabled = e.target.checked;
              patch((x) => (x.id === r.id ? { ...x, enabled } : x));
              submit(() =>
                act("save_rule", { ...r, enabled }, undefined, {
                  invalidate: [["rules"]],
                }),
              ).catch(() => cache.invalidateQueries({ queryKey: ["rules"] }));
            }}
          />
          <span>
            <strong>{r.name}</strong>
            <small>
              {r.field} {r.is_regex ? "matches" : "contains"}{" "}
              {(r.pattern || "").split("\n").filter(Boolean).join(" · ")} →{" "}
              {r.action} {r.value}
            </small>
          </span>
          <IconButton
            icon={Pencil}
            label={"Edit " + r.name}
            onClick={() => setSheet(r)}
          />
          <IconButton
            icon={Trash2}
            label={"Delete " + r.name}
            onClick={() => {
              if (!confirm(`Delete the rule “${r.name}”?`)) return;
              submit(() =>
                act("delete_rule", { id: r.id }, "Rule deleted", {
                  invalidate: [["rules"]],
                }),
              );
            }}
          />
        </div>
      ))}
      {!rules.data?.length && <p className="muted">No rules yet.</p>}
      <RulePreview api={api} act={act} />
      {sheet && (
        <RuleSheet
          data={data}
          act={act}
          api={api}
          rule={sheet.id ? sheet : null}
          onClose={() => setSheet(null)}
          Modal={Modal}
          Field={Field}
        />
      )}
    </>
  );
}
