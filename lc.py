#!/usr/bin/env python3
"""Fetch a LeetCode solution by problem number and copy it to the clipboard.

Solutions come from the open-source doocs/leetcode repository on GitHub.

Usage:
    lc 88                 # copy the Python solution for problem 88
    lc 88 -l cpp          # copy the C++ solution
    lc 88 -n 2            # copy the 2nd approach (Solution2.*), if there is one
    lc 88 -s              # also print the solution to the terminal
    lc two sum            # look a problem up by title instead of number

Set LC_LANG (e.g. `export LC_LANG=java`) to change the default language.
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

RAW = "https://raw.githubusercontent.com/doocs/leetcode/main"
INDEX_URL = f"{RAW}/solution/README_EN.md"
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "leetcode-solutions"
INDEX_MAX_AGE = 7 * 24 * 3600

LANGS = {
    "py": "py", "python": "py", "python3": "py",
    "cpp": "cpp", "c++": "cpp",
    "c": "c",
    "java": "java",
    "go": "go", "golang": "go",
    "js": "js", "javascript": "js",
    "ts": "ts", "typescript": "ts",
    "rs": "rs", "rust": "rs",
    "cs": "cs", "c#": "cs", "csharp": "cs",
    "kt": "kt", "kotlin": "kt",
    "swift": "swift",
    "rb": "rb", "ruby": "rb",
    "php": "php",
    "scala": "scala",
    "sql": "sql", "mysql": "sql",
    "sh": "sh", "bash": "sh", "shell": "sh",
}

# Code-fence tags used in the problem READMEs, mapped to file extensions.
FENCE_TO_EXT = {
    "python": "py", "cpp": "cpp", "c": "c", "java": "java", "go": "go",
    "js": "js", "javascript": "js", "ts": "ts", "typescript": "ts",
    "rust": "rs", "cs": "cs", "kotlin": "kt", "swift": "swift", "rb": "rb",
    "ruby": "rb", "php": "php", "scala": "scala", "sql": "sql", "bash": "sh",
}

ROW_RE = re.compile(r"^\|\s*(\d+)\s*\|\s*\[(.+?)\]\((/solution/[^)]+)/README_EN\.md\)\s*\|(.*?)\|\s*(Easy|Medium|Hard)\s*\|")


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "lc-solutions"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read().decode("utf-8")


def load_index(force=False):
    path = CACHE_DIR / "index.md"
    if force or not path.exists() or time.time() - path.stat().st_mtime > INDEX_MAX_AGE:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(fetch(INDEX_URL), encoding="utf-8")
    problems = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        m = ROW_RE.match(line)
        if m:
            num, title, folder, tags, difficulty = m.groups()
            problems[int(num)] = (title, urllib.parse.unquote(folder), difficulty, re.findall(r"`([^`]+)`", tags))
    return problems


def find_problem(query):
    for force in (False, True):  # retry with a fresh index if not found
        problems = load_index(force)
        if query.isdigit():
            if int(query) in problems:
                return int(query), problems[int(query)]
        else:
            q = query.lower()
            matches = [(n, p) for n, p in problems.items() if p[0].lower() == q]
            matches = matches or [(n, p) for n, p in problems.items() if q in p[0].lower()]
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                print("Several problems match, use the number:", file=sys.stderr)
                for n, p in sorted(matches)[:20]:
                    print(f"  {n:>4}  {p[0]}", file=sys.stderr)
                sys.exit(1)
    sys.exit(f"Problem '{query}' not found.")


def available_langs(folder):
    """List the languages present in the problem README (used when a file is missing)."""
    try:
        readme = fetch(RAW + urllib.parse.quote(folder) + "/README_EN.md")
    except urllib.error.URLError:
        return []
    tags = re.findall(r"^```(\w+)", readme, re.MULTILINE)
    return sorted({FENCE_TO_EXT[t] for t in tags if t in FENCE_TO_EXT})


class SolutionError(Exception):
    pass


def get_solution(num, title, folder, ext, approach=1):
    """Return (code, ext) for a problem; raises SolutionError if there is no such file."""
    suffix = "" if approach == 1 else str(approach)
    base = RAW + urllib.parse.quote(folder)
    try:
        return fetch(f"{base}/Solution{suffix}.{ext}"), ext
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
    langs = available_langs(folder)
    # Database problems only have SQL solutions.
    if ext != "sql" and langs == ["sql"]:
        return fetch(f"{base}/Solution{suffix}.sql"), "sql"
    msg = f"No Solution{suffix}.{ext} for {num}. {title}."
    if suffix and ext in langs:
        msg += f" This problem has no approach #{approach} in {ext}."
    elif langs:
        msg += f" Available languages: {', '.join(langs)}"
    raise SolutionError(msg)


def copy_to_clipboard(text):
    candidates = []
    if os.environ.get("WAYLAND_DISPLAY"):
        candidates.append(["wl-copy"])
    candidates += [["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"],
                   ["pbcopy"], ["clip.exe"]]
    for cmd in candidates:
        if shutil.which(cmd[0]):
            subprocess.run(cmd, input=text.encode("utf-8"), check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
    return False


def main():
    parser = argparse.ArgumentParser(description="Copy a LeetCode solution to the clipboard.")
    parser.add_argument("problem", nargs="+", help="problem number or title")
    parser.add_argument("-l", "--lang", default=os.environ.get("LC_LANG", "py"),
                        help="language (py, cpp, java, go, js, ts, rs, cs, kt, swift, php, sql, ...)")
    parser.add_argument("-n", "--approach", type=int, default=1, help="approach number (default 1)")
    parser.add_argument("-s", "--show", action="store_true", help="also print the solution")
    args = parser.parse_args()

    ext = LANGS.get(args.lang.lower())
    if not ext:
        sys.exit(f"Unknown language '{args.lang}'. Try one of: {', '.join(sorted(set(LANGS.values())))}")

    try:
        num, (title, folder, difficulty, _tags) = find_problem(" ".join(args.problem))
        code, ext = get_solution(num, title, folder, ext, args.approach)
    except SolutionError as e:
        sys.exit(str(e))
    except urllib.error.URLError as e:
        sys.exit(f"Network error: {e}")

    print(f"{num}. {title} [{difficulty}] ({ext})")
    if args.show:
        print(code)
    if copy_to_clipboard(code):
        print("Solution copied to clipboard.")
    else:
        print("No clipboard tool found (install wl-clipboard or xclip). Solution:\n")
        print(code)


if __name__ == "__main__":
    main()
