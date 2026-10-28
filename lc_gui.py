#!/usr/bin/env python3
"""GTK4 / libadwaita front end for lc.py: search a problem, click it, the solution is copied."""

import json
import re
import sys
import threading
import urllib.error
import urllib.parse
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango  # noqa: E402

try:  # optional syntax highlighting (Arch: gtksourceview5)
    gi.require_version("GtkSource", "5")
    from gi.repository import GtkSource
except (ValueError, ImportError):
    GtkSource = None

import lc  # noqa: E402

APP_ID = "io.github.lcsolutions"
HERE = Path(__file__).resolve().parent
SETTINGS = lc.CACHE_DIR / "gui.json"

# (display name, file extension) in dropdown order.
LANGUAGES = [
    ("Python", "py"), ("C++", "cpp"), ("Java", "java"), ("Go", "go"),
    ("JavaScript", "js"), ("TypeScript", "ts"), ("Rust", "rs"), ("C#", "cs"),
    ("C", "c"), ("Kotlin", "kt"), ("Swift", "swift"), ("PHP", "php"),
    ("Ruby", "rb"), ("Scala", "scala"), ("SQL", "sql"), ("Bash", "sh"),
]
DIFFICULTIES = ["Easy", "Medium", "Hard"]
# File extension -> GtkSourceView language id.
SOURCE_LANG = {
    "py": "python3", "cpp": "cpp", "java": "java", "go": "go", "js": "js",
    "ts": "typescript", "rs": "rust", "cs": "c-sharp", "c": "c", "kt": "kotlin",
    "swift": "swift", "php": "php", "rb": "ruby", "scala": "scala", "sql": "sql", "sh": "sh",
}


# ---------- reading the problem README ----------

MATH_SYMBOLS = {
    "times": "×", "cdot": "·", "le": "≤", "leq": "≤", "ge": "≥", "geq": "≥", "ne": "≠", "neq": "≠",
    "lt": "<", "gt": ">", "sum": "Σ", "prod": "∏", "infty": "∞", "in": "∈", "notin": "∉",
    "to": "→", "rightarrow": "→", "leftarrow": "←", "lfloor": "⌊", "rfloor": "⌋", "lceil": "⌈",
    "rceil": "⌉", "sqrt": "√", "ldots": "…", "cdots": "⋯", "dots": "…", "pm": "±", "oplus": "⊕",
    "land": "∧", "lor": "∨", "neg": "¬", "approx": "≈", "equiv": "≡", "bmod": "mod", "pmod": "mod",
    "alpha": "α", "beta": "β", "sigma": "σ", "Sigma": "Σ", "cup": "∪", "cap": "∩", "subseteq": "⊆",
    "forall": "∀", "exists": "∃", "mid": "|", "vert": "|", "lvert": "|", "rvert": "|",
    "left": "", "right": "", "quad": " ", "qquad": " ",
}
SUPERSCRIPT = dict(zip("0123456789+-=()ni", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿⁱ"))
SUBSCRIPT = dict(zip("0123456789+-=()ijkmn", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ᵢⱼₖₘₙ"))


def script(text, table, mark):
    text = text.replace(" ", "")
    if all(ch in table for ch in text):
        return "".join(table[ch] for ch in text)
    return f"{mark}({text})" if len(text) > 1 else mark + text


def matrix_to_text(m):
    rows = [row for row in re.split(r"\\\\", m.group(1)) if row.strip()]
    return "[" + "; ".join("  ".join(cell.strip() for cell in row.split("&")) for row in rows) + "]"


def tex_to_text(s):
    """Turn inline LaTeX like `O(n \\log n)` into readable Unicode."""
    s = re.sub(r"\\begin\{[pbvB]?matrix\}(.*?)\\end\{[pbvB]?matrix\}", matrix_to_text, s, flags=re.DOTALL)
    s = re.sub(r"\\(?:begin|end)\{\w+\*?\}", "", s)
    s = s.replace("\\{", "\x01").replace("\\}", "\x02")
    s = re.sub(r"\\(?:textit|textbf|text|mathrm|mathit|mathbf|texttt|operatorname|boldsymbol)\{([^{}]*)\}", r"\1", s)
    s = re.sub(r"\\frac\{([^{}]*)\}\{([^{}]*)\}", r"(\1)/(\2)", s)
    s = re.sub(r"\\([A-Za-z]+)", lambda m: MATH_SYMBOLS.get(m.group(1), m.group(1)), s)
    s = re.sub(r"\^\{([^{}]*)\}", lambda m: script(m.group(1), SUPERSCRIPT, "^"), s)
    s = re.sub(r"\^(\w)", lambda m: script(m.group(1), SUPERSCRIPT, "^"), s)
    s = re.sub(r"_\{([^{}]*)\}", lambda m: script(m.group(1), SUBSCRIPT, "_"), s)
    s = re.sub(r"_(\w)(?!\w)", lambda m: script(m.group(1), SUBSCRIPT, "_"), s)
    s = s.replace("{", "").replace("}", "").replace("\x01", "{").replace("\x02", "}")
    s = re.sub(r"\\[,!; ]", " ", s).replace("\\_", "_").replace("\\%", "%").replace("\\", "")
    return re.sub(r" {2,}", " ", s).strip()


def markdown_to_markup(md):
    """A small Markdown + inline-math subset rendered as Pango markup."""
    md = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", md)               # images
    md = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", md)           # links -> text
    md = re.sub(r"<[^>]+>", "", md)                             # stray HTML
    md = re.sub(r"^\s*[-*] ", "•  ", md, flags=re.MULTILINE)   # bullets
    out = []
    for i, part in enumerate(re.split(r"\$\$?(.+?)\$\$?", md, flags=re.DOTALL)):
        if i % 2:
            out.append(GLib.markup_escape_text(tex_to_text(part)))
            continue
        part = GLib.markup_escape_text(part)
        part = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", part)
        part = re.sub(r"`([^`]+)`", r"<tt>\1</tt>", part)
        out.append(part)
    return re.sub(r"\n{3,}", "\n\n", "".join(out)).strip()


def find_complexity(text, kind):
    m = re.search(rf"{kind} complexity[^$.]{{0,24}}\$([^$]+)\$", text, re.IGNORECASE)
    return tex_to_text(m.group(1)) if m else None


def parse_readme(readme):
    """Split a doocs README_EN.md into approaches with their explanation and code per language."""
    start = readme.find("## Solutions")
    if start < 0:
        return []
    body = readme[start:].split("<!-- problem:end -->")[0]
    parts = re.split(r"^### (.+)$", body, flags=re.MULTILINE)
    chunks = list(zip(parts[1::2], parts[2::2])) or [("Solution", parts[0])]

    approaches = []
    for heading, chunk in chunks:
        prose, _, tabs = chunk.partition("<!-- tabs:start -->")
        intuition = ""
        m = re.search(r"<!-- thinking:start -->(.*?)<!-- thinking:end -->", prose, re.DOTALL)
        if m:
            lines = [re.sub(r"^>\s?", "", line) for line in m.group(1).strip().splitlines()]
            intuition = "\n".join(line for line in lines if line.strip() != "**Thinking**").strip()
            prose = prose.replace(m.group(0), "")
        prose = re.sub(r"<!--.*?-->", "", prose, flags=re.DOTALL).strip()

        code = {}
        for tag, source in re.findall(r"^```(\w+)\n(.*?)^```", tabs, re.MULTILINE | re.DOTALL):
            ext = lc.FENCE_TO_EXT.get(tag)
            if ext and ext not in code:
                code[ext] = source.rstrip() + "\n"
        if not code:
            continue
        name = re.sub(r"^Solution\s*\d*\s*[:：]?\s*", "", heading.strip()) or heading.strip()
        approaches.append({
            "name": name,
            "intuition": markdown_to_markup(intuition) if intuition else "",
            "explanation": markdown_to_markup(prose) if prose else "",
            "time": find_complexity(prose, "time"),
            "space": find_complexity(prose, "space"),
            "code": code,
        })
    return approaches


def load_approaches(num, title, folder, preferred_ext):
    readme = lc.fetch(lc.RAW + urllib.parse.quote(folder) + "/README_EN.md")
    approaches = parse_readme(readme)
    if approaches:
        return approaches
    # Unusual README layout: fall back to the plain Solution file.
    code, ext = lc.get_solution(num, title, folder, preferred_ext)
    return [{"name": "Solution", "intuition": "", "explanation": "", "time": None, "space": None,
             "code": {ext: code}}]


def leetcode_url(title):
    slug = re.sub(r"[^a-z0-9 -]", "", title.lower())
    slug = re.sub(r"[\s-]+", "-", slug).strip("-")
    return f"https://leetcode.com/problems/{slug}/"


def load_settings():
    try:
        return json.loads(SETTINGS.read_text())
    except (OSError, ValueError):
        return {}


def save_settings(settings):
    try:
        SETTINGS.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS.write_text(json.dumps(settings))
    except OSError:
        pass


# ---------- small widget helpers ----------

def label(text="", *classes, **props):
    props.setdefault("xalign", 0)
    widget = Gtk.Label(label=text, **props)
    for cls in classes:
        widget.add_css_class(cls)
    return widget


def segmented(labels, on_change):
    """An Apple-style segmented control; on_change(index) fires when the choice changes."""
    box = Gtk.Box(halign=Gtk.Align.START)
    box.add_css_class("lc-segmented")
    buttons = []
    for i, text in enumerate(labels):
        button = Gtk.ToggleButton(child=label(text, ellipsize=Pango.EllipsizeMode.END, xalign=0.5))
        if buttons:
            button.set_group(buttons[0])
        box.append(button)
        buttons.append(button)
    buttons[0].set_active(True)
    for i, button in enumerate(buttons):  # connect after the initial choice so it doesn't fire
        button.connect("toggled", lambda b, i=i: b.get_active() and on_change(i))
    return box, buttons


def prose_view(*classes):
    """Read-only wrapped text with comfortable line spacing (labels clip glyphs when spaced out)."""
    view = Gtk.TextView(editable=False, cursor_visible=False, wrap_mode=Gtk.WrapMode.WORD_CHAR,
                        pixels_inside_wrap=5, pixels_below_lines=5, can_focus=False)
    view.add_css_class("lc-prose")
    for cls in classes:
        view.add_css_class(cls)
    return view


def placeholder(title, text):
    """A quiet centred message, like macOS Mail's "No Message Selected"."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                  halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, margin_bottom=48)
    box.append(label(title, "lc-placeholder-title", xalign=0.5))
    body = label(text, "lc-placeholder-text", xalign=0.5, justify=Gtk.Justification.CENTER, wrap=True, max_width_chars=48)
    box.append(body)
    return box, body


# ---------- window ----------

class Window(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="LeetCode Solutions")
        self.add_css_class("lc")
        self.set_default_size(1180, 780)
        self.set_size_request(380, 480)

        self.problems = {}
        self.parsed = {}          # problem number -> approaches
        self.current = None       # problem number shown on the right
        self.approaches = []
        self.approach = 0
        self.lang_exts = []       # extensions offered by the language dropdown, in order
        self.code = ""
        self.difficulty = None    # sidebar filter
        self.request_id = 0
        self.syncing = False      # True while the code updates widgets (ignore their signals)
        self.copied_timer = 0
        self.settings = load_settings()

        self.split = Adw.OverlaySplitView(min_sidebar_width=300, max_sidebar_width=360,
                                          sidebar_width_fraction=0.3)
        self.split.set_sidebar(self.build_sidebar())
        self.split.set_content(self.build_content())
        self.toasts = Adw.ToastOverlay(child=self.split)
        self.set_content(self.toasts)

        narrow = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 760sp"))
        narrow.add_setter(self.split, "collapsed", True)
        self.add_breakpoint(narrow)

        self.add_shortcuts()
        self.search.set_key_capture_widget(self)
        self.load_index()

    def add_shortcuts(self):
        controller = Gtk.ShortcutController(scope=Gtk.ShortcutScope.GLOBAL)

        def focus_search(*_):
            self.split.set_show_sidebar(True)
            self.search.grab_focus()
            self.search.select_region(0, -1)
            return True

        def copy(*_):
            self.copy()
            return True

        for trigger, callback in (("<Control>k|<Control>f", focus_search), ("<Control><Shift>c", copy)):
            controller.add_shortcut(Gtk.Shortcut(trigger=Gtk.ShortcutTrigger.parse_string(trigger),
                                                 action=Gtk.CallbackAction.new(callback)))
        self.add_controller(controller)

    # ---------- layout ----------

    def build_sidebar(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.add_css_class("lc-sidebar")

        heading = Gtk.Box(spacing=8, margin_start=16, margin_end=16, margin_bottom=12)
        heading.append(label("Problems", "lc-sidebar-title", hexpand=True))
        self.count = label("", "lc-count", valign=Gtk.Align.BASELINE_CENTER)
        heading.append(self.count)
        box.append(heading)

        self.search = Gtk.SearchEntry(placeholder_text="Search number or title",
                                      margin_start=16, margin_end=16)
        self.search.add_css_class("lc-search")
        self.search.connect("search-changed", lambda *_: self.refilter())
        self.search.connect("activate", self.on_search_activate)
        self.search.connect("stop-search", lambda e: e.set_text(""))
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.on_search_key)
        self.search.add_controller(keys)
        box.append(self.search)

        filters, _ = segmented(["All", *DIFFICULTIES], self.on_difficulty)
        filters.set_halign(Gtk.Align.FILL)
        filters.set_homogeneous(True)
        filters.set_margin_start(16)
        filters.set_margin_end(16)
        filters.set_margin_top(12)
        filters.set_margin_bottom(8)
        box.append(filters)

        self.items = Gtk.StringList()
        self.filter = Gtk.CustomFilter.new(self.match_item)
        self.filtered = Gtk.FilterListModel(model=self.items, filter=self.filter)
        self.filtered.connect("items-changed", lambda *_: self.update_count())
        self.selection = Gtk.SingleSelection(model=self.filtered, autoselect=False, can_unselect=True)
        self.selection.connect("notify::selected-item", self.on_selected)

        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self.setup_row)
        factory.connect("bind", self.bind_row)
        self.list = Gtk.ListView(model=self.selection, factory=factory)
        self.list.add_css_class("lc-list")
        self.list.connect("activate", lambda *_: self.copy())

        self.list_stack = Gtk.Stack(vexpand=True, transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.list_stack.add_named(Adw.Spinner(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
                                              width_request=28, height_request=28), "loading")
        self.list_stack.add_named(Gtk.ScrolledWindow(child=self.list, hscrollbar_policy=Gtk.PolicyType.NEVER), "list")
        empty = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, spacing=4)
        empty.append(label("No Results", "lc-section-title", xalign=0.5))
        empty.append(label("Try a different number or title.", "lc-hint", xalign=0.5))
        self.list_stack.add_named(empty, "empty")
        self.list_error = label("", "lc-hint", xalign=0.5, wrap=True, valign=Gtk.Align.CENTER,
                                justify=Gtk.Justification.CENTER, margin_start=24, margin_end=24)
        self.list_stack.add_named(self.list_error, "error")
        box.append(self.list_stack)

        view = Adw.ToolbarView(content=box)
        header = Adw.HeaderBar(show_title=False)
        view.add_top_bar(header)
        view.add_css_class("lc-sidebar")
        return view

    def build_content(self):
        header = Adw.HeaderBar(show_title=False)
        toggle = Gtk.ToggleButton(icon_name="sidebar-show-symbolic", tooltip_text="Toggle Sidebar")
        self.split.bind_property("show-sidebar", toggle, "active",
                                 GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE)
        header.pack_start(toggle)
        self.open_button = Gtk.Button(label="LeetCode ↗", tooltip_text="Open on leetcode.com", visible=False)
        self.open_button.connect("clicked", self.on_open_leetcode)
        header.pack_end(self.open_button)

        self.content_stack = Gtk.Stack(vexpand=True, transition_type=Gtk.StackTransitionType.CROSSFADE,
                                       transition_duration=220)
        empty, _ = placeholder("No Problem Selected",
                               "Search by number or title. Opening a problem copies its solution.")
        hints = Gtk.Box(spacing=4, halign=Gtk.Align.CENTER, margin_top=16)
        for keycaps, text in ((["Ctrl", "K"], "search"), (["↵"], "open & copy"), (["Ctrl", "⇧", "C"], "copy again")):
            for cap in keycaps:
                hints.append(label(cap, "lc-kbd"))
            hints.append(label(text, "lc-hint", margin_end=16, margin_start=4))
        empty.append(hints)
        self.content_stack.add_named(empty, "empty")
        self.content_stack.add_named(Adw.Spinner(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
                                                 width_request=32, height_request=32), "loading")
        self.content_stack.add_named(self.build_page(), "page")
        error, self.error_text = placeholder("Couldn't Load Problem", "")
        retry = Gtk.Button(label="Try Again", halign=Gtk.Align.CENTER, margin_top=16)
        retry.add_css_class("suggested-action")
        retry.connect("clicked", lambda *_: self.current is not None and self.open_problem(self.current))
        error.append(retry)
        self.content_stack.add_named(error, "error")

        view = Adw.ToolbarView(content=self.content_stack)
        view.add_top_bar(header)
        return view

    def build_page(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_top=8, margin_bottom=48,
                       margin_start=32, margin_end=32)

        eyebrow = Gtk.Box(spacing=8)
        self.number_label = label("", "lc-eyebrow", valign=Gtk.Align.CENTER)
        self.badge = label("", "lc-badge", valign=Gtk.Align.CENTER)
        eyebrow.append(self.number_label)
        eyebrow.append(self.badge)
        page.append(eyebrow)

        self.title_label = label("", "lc-large-title", wrap=True, margin_top=8, selectable=True)
        page.append(self.title_label)

        self.tags = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=30,
                                row_spacing=8, column_spacing=8, margin_top=16, can_focus=False)
        self.tags.add_css_class("lc-tags")
        page.append(self.tags)

        approach_head = Gtk.Box(spacing=12, margin_top=32, margin_bottom=12)
        approach_head.append(label("Approach", "lc-section-title", valign=Gtk.Align.CENTER))
        self.approach_slot = Gtk.ScrolledWindow(vscrollbar_policy=Gtk.PolicyType.NEVER, hexpand=True,
                                                propagate_natural_height=True)
        approach_head.append(self.approach_slot)
        page.append(approach_head)

        # approach card: name, complexity, expandable explanation
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        card.add_css_class("lc-card")
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                        margin_top=16, margin_bottom=16, margin_start=16, margin_end=16)
        top = Gtk.Box(spacing=8)
        self.approach_name = label("", "lc-approach-name", hexpand=True, wrap=True, valign=Gtk.Align.CENTER)
        top.append(self.approach_name)
        self.stats = Gtk.Box(spacing=8, valign=Gtk.Align.CENTER)
        top.append(self.stats)
        inner.append(top)

        self.disclosure = Gtk.Button(halign=Gtk.Align.START)
        self.disclosure.add_css_class("lc-disclosure")
        disclosure_box = Gtk.Box(spacing=4)
        disclosure_box.append(Gtk.Image(icon_name="pan-end-symbolic"))
        self.disclosure_label = label("Show explanation")
        disclosure_box.append(self.disclosure_label)
        self.disclosure.set_child(disclosure_box)
        self.disclosure.connect("clicked", self.on_disclosure)
        inner.append(self.disclosure)

        self.revealer = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN,
                                     transition_duration=280)
        explain = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=8)
        self.intuition_head = label("INTUITION", "lc-subheading")
        self.intuition = prose_view("lc-prose-quiet")
        self.explanation_head = label("HOW IT WORKS", "lc-subheading", margin_top=16)
        self.explanation = prose_view()
        for w in (self.intuition_head, self.intuition, self.explanation_head, self.explanation):
            explain.append(w)
        self.revealer.set_child(explain)
        inner.append(self.revealer)
        card.append(inner)
        page.append(card)

        # code card
        code_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_top=16, overflow=Gtk.Overflow.HIDDEN)
        code_card.add_css_class("lc-card")
        code_card.add_css_class("lc-code-card")
        code_header = Gtk.Box(spacing=8)
        code_header.add_css_class("lc-code-header")
        self.lang = Gtk.DropDown(model=Gtk.StringList())
        self.lang.add_css_class("lc-lang")
        self.lang.connect("notify::selected", self.on_language)
        code_header.append(self.lang)
        code_header.append(Gtk.Box(hexpand=True))
        self.copy_content = Adw.ButtonContent(icon_name="edit-copy-symbolic", label="Copy")
        self.copy_button = Gtk.Button(child=self.copy_content, tooltip_text="Copy (Ctrl+Shift+C)")
        self.copy_button.add_css_class("lc-copy")
        self.copy_button.connect("clicked", lambda *_: self.copy())
        code_header.append(self.copy_button)
        code_card.append(code_header)

        text_args = dict(editable=False, monospace=True, cursor_visible=False, pixels_above_lines=2,
                         pixels_below_lines=3, top_margin=16, bottom_margin=16, left_margin=8, right_margin=16)
        if GtkSource:
            GtkSource.StyleSchemeManager.get_default().prepend_search_path(str(HERE / "styles"))
            self.text = GtkSource.View(show_line_numbers=True, highlight_current_line=False, **text_args)
            Adw.StyleManager.get_default().connect("notify::dark", lambda *_: self.update_scheme())
            self.update_scheme()
        else:
            self.text = Gtk.TextView(**text_args)
        self.text.add_css_class("lc-code")
        code_card.append(Gtk.ScrolledWindow(child=self.text, vscrollbar_policy=Gtk.PolicyType.NEVER,
                                            propagate_natural_height=True))
        page.append(code_card)

        clamp = Adw.Clamp(child=page, maximum_size=880, tightening_threshold=640)
        self.page_scroll = Gtk.ScrolledWindow(child=clamp, hscrollbar_policy=Gtk.PolicyType.NEVER)
        return self.page_scroll

    def update_scheme(self):
        name = "lc-dark" if Adw.StyleManager.get_default().get_dark() else "lc-light"
        self.text.get_buffer().set_style_scheme(GtkSource.StyleSchemeManager.get_default().get_scheme(name))

    # ---------- problem list ----------

    def setup_row(self, _factory, item):
        row = Gtk.Box(spacing=12)
        row.append(label("", "lc-num", width_chars=4, xalign=1))
        row.append(label("", "lc-row-title", hexpand=True, ellipsize=Pango.EllipsizeMode.END))
        dot = Gtk.Box(valign=Gtk.Align.CENTER, margin_end=4)
        row.append(dot)
        item.set_child(row)

    def bind_row(self, _factory, item):
        num_label = item.get_child().get_first_child()
        title_label = num_label.get_next_sibling()
        dot = title_label.get_next_sibling()
        num = int(item.get_item().get_string())
        title, _, difficulty, _ = self.problems[num]
        num_label.set_label(str(num))
        title_label.set_label(title)
        dot.set_css_classes(["lc-dot", difficulty.lower()])
        dot.set_tooltip_text(difficulty)

    def match_item(self, item):
        num = item.get_string()
        title, _, difficulty, _ = self.problems[int(num)]
        if self.difficulty and difficulty != self.difficulty:
            return False
        query = self.search.get_text().strip().lower()
        if not query:
            return True
        if query.isdigit():
            return num.startswith(query)
        return all(word in title.lower() for word in query.split())

    def refilter(self):
        self.filter.changed(Gtk.FilterChange.DIFFERENT)
        self.update_count()

    def update_count(self):
        if not self.problems:
            return
        n = self.filtered.get_n_items()
        filtered = self.difficulty or self.search.get_text().strip()
        self.count.set_label(f"{n:,} {'results' if filtered else 'problems'}" if n != 1 else "1 result")
        self.list_stack.set_visible_child_name("list" if n else "empty")

    def on_difficulty(self, index):
        self.difficulty = None if index == 0 else DIFFICULTIES[index - 1]
        if self.problems:
            self.refilter()

    def on_search_activate(self, _entry):
        # search-changed is debounced, so apply the latest text before picking the top match.
        self.refilter()
        if not self.filtered.get_n_items():
            return
        if self.selection.get_selected() == 0:
            self.copy()
        else:
            self.selection.set_selected(0)
        self.list.scroll_to(0, Gtk.ListScrollFlags.NONE, None)

    def on_search_key(self, _controller, keyval, _code, _state):
        if keyval == Gdk.KEY_Down and self.filtered.get_n_items():
            self.list.scroll_to(0, Gtk.ListScrollFlags.FOCUS | Gtk.ListScrollFlags.SELECT, None)
            return True
        return False

    def on_selected(self, *_):
        item = self.selection.get_selected_item()
        if item is not None and int(item.get_string()) != self.current:
            self.open_problem(int(item.get_string()))
            if self.split.get_collapsed():
                self.split.set_show_sidebar(False)

    # ---------- loading ----------

    def run_async(self, work, done):
        """Run work() in a thread and pass its result (or exception) to done() on the UI thread."""
        def target():
            try:
                result = work()
            except Exception as e:  # reported in the UI
                result = e
            GLib.idle_add(done, result)
        threading.Thread(target=target, daemon=True).start()

    def load_index(self):
        def done(result):
            if isinstance(result, Exception):
                self.list_error.set_label(f"Couldn't load the problem list.\n{result}")
                self.list_stack.set_visible_child_name("error")
                return
            self.problems = result
            self.items.splice(0, 0, [str(n) for n in sorted(result)])
            self.update_count()
        self.list_stack.set_visible_child_name("loading")
        self.run_async(lc.load_index, done)

    def open_problem(self, num):
        self.current = num
        title, folder, difficulty, tags = self.problems[num]
        self.request_id += 1
        request_id = self.request_id

        def done(result):
            if request_id != self.request_id:
                return  # a newer request superseded this one
            if isinstance(result, Exception):
                if isinstance(result, lc.SolutionError):
                    message = str(result)
                elif isinstance(result, urllib.error.URLError):
                    message = f"Check your connection and try again.\n{result.reason}"
                else:
                    message = str(result)
                self.error_text.set_label(message)
                self.content_stack.set_visible_child_name("error")
                return
            self.parsed[num] = result
            self.show_problem(num, result)

        if num in self.parsed:
            self.show_problem(num, self.parsed[num])
            return
        # Only show the spinner if loading takes a moment; avoids a flash on fast connections.
        GLib.timeout_add(180, lambda: request_id == self.request_id and num not in self.parsed
                         and self.content_stack.set_visible_child_name("loading"))
        self.run_async(lambda: load_approaches(num, title, folder, self.preferred_ext()), done)

    def show_problem(self, num, approaches):
        title, _, difficulty, tags = self.problems[num]
        self.number_label.set_label(f"PROBLEM {num}")
        self.badge.set_label(difficulty.upper())
        self.badge.set_css_classes(["lc-badge", difficulty.lower()])
        self.title_label.set_label(title)
        self.tags.remove_all()
        for tag in tags:
            self.tags.append(label(tag, "lc-tag"))
        self.tags.set_visible(bool(tags))
        self.open_button.set_visible(True)

        self.approaches = approaches
        if len(approaches) > 1:
            control, _ = segmented([a["name"] for a in approaches], self.show_approach)
            self.approach_slot.set_child(control)
            self.approach_slot.set_visible(True)
        else:
            self.approach_slot.set_visible(False)
        self.page_scroll.get_vadjustment().set_value(0)
        self.show_approach(0)
        self.content_stack.set_visible_child_name("page")

    def show_approach(self, index):
        self.approach = index
        approach = self.approaches[index]
        self.approach_name.set_label(approach["name"])

        while child := self.stats.get_first_child():
            self.stats.remove(child)
        for key in ("time", "space"):
            if approach[key]:
                chip = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
                chip.add_css_class("lc-stat")
                chip.append(label(key.upper(), "lc-stat-key"))
                chip.append(label(approach[key], "lc-stat-value"))
                self.stats.append(chip)

        for head, body, markup in ((self.intuition_head, self.intuition, approach["intuition"]),
                                   (self.explanation_head, self.explanation, approach["explanation"])):
            buffer = body.get_buffer()
            buffer.set_text("")
            buffer.insert_markup(buffer.get_start_iter(), markup, -1)
            head.set_visible(bool(markup))
            body.set_visible(bool(markup))
        self.explanation_head.set_margin_top(16 if approach["intuition"] else 0)
        self.disclosure.set_visible(bool(approach["intuition"] or approach["explanation"]))

        # languages this approach has, keeping the user's favourite when possible
        self.lang_exts = [ext for _, ext in LANGUAGES if ext in approach["code"]]
        preferred = self.preferred_ext()
        ext = preferred if preferred in self.lang_exts else ("py" if "py" in self.lang_exts else self.lang_exts[0])
        self.syncing = True
        names = dict((e, n) for n, e in LANGUAGES)
        self.lang.set_model(Gtk.StringList.new([names[e] for e in self.lang_exts]))
        self.lang.set_selected(self.lang_exts.index(ext))
        self.syncing = False
        self.show_code(ext)

    def show_code(self, ext):
        self.code = self.approaches[self.approach]["code"][ext]
        buffer = self.text.get_buffer()
        buffer.set_text(self.code.rstrip("\n"))
        if GtkSource:
            buffer.set_language(GtkSource.LanguageManager.get_default().get_language(SOURCE_LANG.get(ext, "")))
        self.copy()

    def preferred_ext(self):
        return self.settings.get("lang") or lc.LANGS.get(lc.os.environ.get("LC_LANG", "py"), "py")

    # ---------- actions ----------

    def on_language(self, *_):
        if self.syncing or not self.lang_exts:
            return
        ext = self.lang_exts[self.lang.get_selected()]
        self.settings["lang"] = ext
        save_settings(self.settings)
        self.show_code(ext)

    def on_disclosure(self, *_):
        opening = not self.revealer.get_reveal_child()
        self.revealer.set_reveal_child(opening)
        self.disclosure_label.set_label("Hide explanation" if opening else "Show explanation")
        if opening:
            self.disclosure.add_css_class("open")
        else:
            self.disclosure.remove_css_class("open")

    def on_open_leetcode(self, *_):
        if self.current is not None:
            Gtk.UriLauncher(uri=leetcode_url(self.problems[self.current][0])).launch(self, None, None, None)

    def copy(self):
        if not self.code:
            return
        # wl-copy/xclip keep the clipboard after the app closes; GTK's clipboard doesn't.
        try:
            copied = lc.copy_to_clipboard(self.code)
        except (OSError, lc.subprocess.CalledProcessError):
            copied = False
        if not copied:
            self.get_clipboard().set(self.code)

        self.copy_content.set_label("Copied")
        self.copy_content.set_icon_name("object-select-symbolic")
        self.copy_button.add_css_class("copied")
        if self.copied_timer:
            GLib.source_remove(self.copied_timer)
        self.copied_timer = GLib.timeout_add(1800, self.reset_copy_button)

    def reset_copy_button(self):
        self.copied_timer = 0
        self.copy_content.set_label("Copy")
        self.copy_content.set_icon_name("edit-copy-symbolic")
        self.copy_button.remove_css_class("copied")
        return False


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

    def do_startup(self):
        Adw.Application.do_startup(self)
        css = Gtk.CssProvider()
        css.load_from_path(str(HERE / "style.css"))
        # App providers don't follow the system light/dark setting on their own (GTK 4.20+).
        style = Adw.StyleManager.get_default()

        def sync_scheme(*_):
            try:
                css.set_property("prefers-color-scheme", Gtk.InterfaceColorScheme.DARK if style.get_dark()
                                 else Gtk.InterfaceColorScheme.LIGHT)
            except (TypeError, AttributeError):
                pass  # older GTK: app providers already follow the system setting
        style.connect("notify::dark", sync_scheme)
        sync_scheme()
        # Above the user theme (Matugen) so the app keeps its own look.
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_USER + 1)

    def do_activate(self):
        window = self.get_active_window() or Window(self)
        window.present()


if __name__ == "__main__":
    sys.exit(App().run(sys.argv))
