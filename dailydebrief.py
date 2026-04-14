"""
DailyDebrief  —  Windows Edition
==================================
Collects data from your day: git commits, shell history, recently modified files.
Feeds everything to a local LLM and outputs a structured 5-section debrief.

Prerequisites:
    1. Install ollama:       https://ollama.com/download
    2. Start ollama server:  ollama serve   (separate terminal)
    3. Pull a model:         ollama pull qwen2.5:3b
    4. Install Python deps:  pip install ollama gitpython rich

Usage:
    python dailydebrief.py
    python dailydebrief.py --model qwen2.5:3b --hours 12 --dir C:/Projects
"""

import argparse
import os
import sys
import time
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.text import Text
    from rich.rule import Rule
    from rich.table import Table
    from rich.progress import Progress, SpinnerColumn, TextColumn
    from rich import print as rprint
except ImportError:
    print("ERROR: rich not installed. Run: pip install rich")
    sys.exit(1)

try:
    import ollama
except ImportError:
    print("ERROR: ollama not installed. Run: pip install ollama")
    sys.exit(1)

# gitpython is optional — we fall back gracefully if not installed
try:
    import git
    HAS_GIT = True
except ImportError:
    HAS_GIT = False

# ── Config ────────────────────────────────────────────────────────────────────

DEFAULT_MODEL   = "qwen2.5:3b"
DEFAULT_HOURS   = 8             # how many hours back to look
DEFAULT_DIR     = str(Path.home())

# File types to track (recently modified)
TRACKED_EXTS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".html", ".css", ".scss",
    ".rs", ".go", ".c", ".cpp", ".h", ".java", ".kt", ".swift",
    ".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".ini", ".env",
    ".sh", ".bat", ".ps1", ".sql",
}

# Directories to skip when scanning for modified files
SKIP_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv", "env",
    ".tox", "dist", "build", ".idea", ".vscode", "site-packages",
}

MAX_FILES_SHOWN   = 20
MAX_COMMITS_SHOWN = 15
MAX_HISTORY_SHOWN = 30

# ── Console ───────────────────────────────────────────────────────────────────

console = Console()

# ── Data collectors ───────────────────────────────────────────────────────────

def collect_git_activity(search_dir: str, hours: int) -> dict:
    """
    Scan for git repositories under search_dir and collect recent commits.
    Returns a dict with found repos and their commit summaries.
    """
    result = {"repos": [], "total_commits": 0, "error": None}

    if not HAS_GIT:
        result["error"] = "gitpython not installed (pip install gitpython)"
        return result

    since = datetime.now() - timedelta(hours=hours)
    search_path = Path(search_dir)

    # Find all .git directories (up to 3 levels deep to avoid being slow)
    git_dirs = []
    try:
        for item in search_path.rglob(".git"):
            if item.is_dir() and not any(
                skip in item.parts for skip in SKIP_DIRS
            ):
                git_dirs.append(item.parent)
            if len(git_dirs) >= 10:   # cap at 10 repos
                break
    except (PermissionError, OSError):
        pass

    # Also check the search_dir itself
    if (search_path / ".git").exists():
        git_dirs.insert(0, search_path)

    if not git_dirs:
        result["error"] = f"No git repos found under {search_dir}"
        return result

    for repo_path in git_dirs[:5]:   # limit to 5 repos
        try:
            repo      = git.Repo(repo_path)
            repo_name = repo_path.name
            commits   = []

            for commit in repo.iter_commits(
                since=since.strftime("%Y-%m-%d %H:%M:%S")
            ):
                commits.append({
                    "hash":    commit.hexsha[:7],
                    "message": commit.message.strip().splitlines()[0][:100],
                    "author":  str(commit.author),
                    "time":    datetime.fromtimestamp(commit.committed_date)
                               .strftime("%H:%M"),
                    "files":   len(commit.stats.files),
                })
                if len(commits) >= MAX_COMMITS_SHOWN:
                    break

            if commits:
                result["repos"].append({
                    "name":    repo_name,
                    "path":    str(repo_path),
                    "branch":  repo.active_branch.name,
                    "commits": commits,
                })
                result["total_commits"] += len(commits)

        except (git.InvalidGitRepositoryError, git.GitCommandError,
                TypeError, ValueError):
            pass

    return result


def collect_modified_files(search_dir: str, hours: int) -> list:
    """
    Find files modified in the last `hours` hours under search_dir.
    Returns a list of dicts with path, extension, modified time.
    """
    since     = time.time() - hours * 3600
    files     = []
    seen_dirs = set()

    try:
        for root, dirs, filenames in os.walk(search_dir):
            # Prune skip dirs in-place
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]

            root_path = Path(root)

            # Skip if we've already been too deep
            depth = len(root_path.relative_to(search_dir).parts)
            if depth > 6:
                dirs.clear()
                continue

            for fname in filenames:
                fpath = root_path / fname
                if fpath.suffix.lower() not in TRACKED_EXTS:
                    continue
                try:
                    mtime = fpath.stat().st_mtime
                    if mtime >= since:
                        files.append({
                            "path":  str(fpath),
                            "name":  fname,
                            "ext":   fpath.suffix.lower(),
                            "mtime": datetime.fromtimestamp(mtime)
                                     .strftime("%H:%M"),
                            "size":  fpath.stat().st_size,
                        })
                except (PermissionError, OSError):
                    pass

    except (PermissionError, OSError):
        pass

    # Sort by most recently modified first
    files.sort(key=lambda f: f["mtime"], reverse=True)
    return files[:MAX_FILES_SHOWN]


def collect_shell_history(hours: int) -> list:
    """
    Read shell command history from common locations on Windows.
    Checks: PowerShell history, cmd history (doskey export), git bash.
    Returns list of recent command strings.
    """
    commands = []
    since    = time.time() - hours * 3600

    # PowerShell history
    ps_history_paths = [
        Path.home() / "AppData/Roaming/Microsoft/Windows/PowerShell"
                     / "PSReadLine/ConsoleHost_history.txt",
        Path.home() / ".config/powershell/PSReadLineHistory.txt",
    ]
    for ps_path in ps_history_paths:
        if ps_path.exists():
            try:
                lines = ps_path.read_text(encoding="utf-8", errors="replace"
                                          ).splitlines()
                # PowerShell history has no timestamps — take the last N lines
                recent = [l.strip() for l in lines[-MAX_HISTORY_SHOWN * 2:]
                          if l.strip() and not l.startswith("#")]
                commands.extend(recent[-MAX_HISTORY_SHOWN:])
                break
            except (PermissionError, OSError):
                pass

    # Git bash / WSL bash history
    bash_history = Path.home() / ".bash_history"
    if bash_history.exists() and not commands:
        try:
            lines = bash_history.read_text(encoding="utf-8", errors="replace"
                                           ).splitlines()
            recent = [l.strip() for l in lines[-MAX_HISTORY_SHOWN * 2:]
                      if l.strip() and not l.startswith("#")]
            commands.extend(recent[-MAX_HISTORY_SHOWN:])
        except (PermissionError, OSError):
            pass

    # Zsh history
    zsh_history = Path.home() / ".zsh_history"
    if zsh_history.exists() and not commands:
        try:
            lines = zsh_history.read_text(encoding="utf-8", errors="replace"
                                          ).splitlines()
            # zsh format: ": timestamp:0;command"
            for line in lines[-MAX_HISTORY_SHOWN * 2:]:
                if line.startswith(": "):
                    parts = line.split(";", 1)
                    if len(parts) == 2:
                        commands.append(parts[1].strip())
                elif line.strip():
                    commands.append(line.strip())
            commands = commands[-MAX_HISTORY_SHOWN:]
        except (PermissionError, OSError):
            pass

    return list(dict.fromkeys(commands))   # deduplicate preserving order


def collect_system_context() -> dict:
    """Collect basic system context: hostname, username, current time."""
    return {
        "hostname": os.environ.get("COMPUTERNAME", "unknown"),
        "username": os.environ.get("USERNAME", os.environ.get("USER", "unknown")),
        "date":     datetime.now().strftime("%A, %B %d %Y"),
        "time":     datetime.now().strftime("%H:%M"),
        "cwd":      str(Path.cwd()),
    }

# ── Data formatter for LLM prompt ─────────────────────────────────────────────

def format_for_prompt(git_data: dict, files: list,
                      history: list, ctx: dict, hours: int) -> str:
    parts = []

    parts.append(f"=== DAILY ACTIVITY REPORT ===")
    parts.append(f"User: {ctx['username']}  |  Date: {ctx['date']}  |  Time: {ctx['time']}")
    parts.append(f"Period: last {hours} hours")
    parts.append("")

    # Git commits
    if git_data["total_commits"] > 0:
        parts.append(f"--- GIT COMMITS ({git_data['total_commits']} total) ---")
        for repo in git_data["repos"]:
            parts.append(f"Repo: {repo['name']} (branch: {repo['branch']})")
            for c in repo["commits"]:
                parts.append(f"  [{c['time']}] {c['hash']} — {c['message']} ({c['files']} files)")
    elif git_data["error"]:
        parts.append(f"--- GIT: {git_data['error']} ---")
    else:
        parts.append("--- GIT: No commits in this period ---")

    parts.append("")

    # Modified files
    if files:
        parts.append(f"--- MODIFIED FILES ({len(files)} files) ---")
        by_ext = {}
        for f in files:
            by_ext.setdefault(f["ext"], []).append(f["name"])
        for ext, names in sorted(by_ext.items()):
            parts.append(f"  {ext}: {', '.join(names[:5])}")
        parts.append(f"  Most recent: {files[0]['name']} at {files[0]['mtime']}")
    else:
        parts.append("--- MODIFIED FILES: None found ---")

    parts.append("")

    # Shell history
    if history:
        parts.append(f"--- SHELL HISTORY (last {len(history)} commands) ---")
        for cmd in history[-20:]:
            parts.append(f"  $ {cmd[:120]}")
    else:
        parts.append("--- SHELL HISTORY: Not available ---")

    return "\n".join(parts)

# ── LLM query ─────────────────────────────────────────────────────────────────

DEBRIEF_PROMPT = """You are a personal productivity analyst. Based on the activity data below, 
write a concise daily debrief with EXACTLY these 5 sections, each on its own line.
Format each section as shown, with the emoji and label exactly as written:

🔨 BUILT: [one sentence about what was built or worked on]
🔥 BROKE: [one sentence about what broke, failed, or caused problems — say "Nothing notable" if none]
📚 LEARNED: [one sentence about a key insight or thing learned today]
⏭ NEXT: [one sentence about the most important next step or task]
💡 INSIGHT: [one sentence of a broader observation or pattern from today's work]

Rules:
- Each section must be exactly ONE sentence.
- Be specific — use actual file names, commit messages, and commands from the data.
- If data is sparse, make reasonable inferences from what IS there.
- Do not add any other text, headers, or explanations outside these 5 lines.

Activity data:
"""

def generate_debrief(model: str, prompt_data: str) -> str:
    """Stream the debrief from the LLM and return the full response."""
    full_prompt = DEBRIEF_PROMPT + prompt_data

    response = ""
    try:
        stream = ollama.chat(
            model=model,
            messages=[{"role": "user", "content": full_prompt}],
            stream=True,
        )
        for chunk in stream:
            content = chunk.get("message", {}).get("content", "")
            response += content
    except ollama.ResponseError as e:
        return f"[LLM error: {e}]"
    except Exception as e:
        return f"[Could not reach ollama: {e}]"

    return response.strip()

# ── Rich display ──────────────────────────────────────────────────────────────

SECTION_STYLES = {
    "🔨 BUILT":   ("bold cyan",    "cyan"),
    "🔥 BROKE":   ("bold red",     "red"),
    "📚 LEARNED": ("bold yellow",  "yellow"),
    "⏭ NEXT":    ("bold green",   "green"),
    "💡 INSIGHT": ("bold magenta", "magenta"),
}

def parse_debrief(text: str) -> list:
    """Parse the 5-section debrief into (label, content) pairs."""
    sections = []
    for line in text.splitlines():
        line = line.strip()
        for label in SECTION_STYLES:
            if line.upper().startswith(label.upper()):
                content = line[len(label):].lstrip(": ").strip()
                sections.append((label, content))
                break
        else:
            # Fallback: include non-empty lines that don't match
            if line and not any(line.upper().startswith(l.upper())
                                for l in SECTION_STYLES):
                if sections:
                    # Append to last section
                    sections[-1] = (sections[-1][0],
                                    sections[-1][1] + " " + line)
    return sections


def display_data_summary(git_data: dict, files: list,
                         history: list, ctx: dict, hours: int):
    """Show a summary table of what data was collected."""
    console.print()
    console.print(Rule("[bold cyan]Data Collected[/bold cyan]", style="cyan"))
    console.print()

    table = Table(show_header=True, header_style="bold dim",
                  border_style="dim", box=None, pad_edge=False)
    table.add_column("Source",  style="cyan",  width=22)
    table.add_column("Count",   style="bold",  width=10, justify="right")
    table.add_column("Details", style="dim")

    # Git
    git_count = git_data["total_commits"]
    git_detail = (f"{len(git_data['repos'])} repo(s)" if git_count
                  else git_data.get("error", "no commits"))
    table.add_row("Git commits", str(git_count) if git_count else "0",
                  git_detail or "")

    # Files
    ext_summary = ", ".join(sorted({f["ext"] for f in files})[:6])
    table.add_row("Modified files", str(len(files)),
                  ext_summary or "none found")

    # History
    table.add_row("Shell commands", str(len(history)),
                  "PowerShell / bash" if history else "not found")

    console.print(table)
    console.print()


def display_debrief(sections: list, ctx: dict, hours: int, model: str):
    """Render the debrief as styled rich panels."""
    console.print()
    console.print(Rule(
        f"[bold cyan]Daily Debrief[/bold cyan]  "
        f"[dim]{ctx['date']}  ·  last {hours}h  ·  {model}[/dim]",
        style="cyan"
    ))
    console.print()

    if not sections:
        console.print("[red]Could not parse debrief sections.[/red]")
        return

    for label, content in sections:
        label_style, border_style = SECTION_STYLES.get(
            label, ("bold white", "white")
        )
        panel = Panel(
            Text(content, style="white"),
            title=f"[{label_style}]{label}[/{label_style}]",
            border_style=border_style,
            padding=(0, 2),
        )
        console.print(panel)

    console.print()
    console.print(
        f"  [dim]Generated {datetime.now().strftime('%H:%M')} "
        f"by {model} via ollama[/dim]"
    )
    console.print()


def display_banner(hours: int, search_dir: str):
    console.print()
    console.print(Rule(style="cyan"))
    console.print(
        "[bold cyan]DailyDebrief[/bold cyan]  "
        "[dim]— Day 13 · BUILDCORED ORCAS[/dim]"
    )
    console.print(
        f"  [dim]Scanning last [bold]{hours}h[/bold] "
        f"in [bold]{search_dir}[/bold][/dim]"
    )
    console.print(Rule(style="cyan"))

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="DailyDebrief — collect your day's activity and generate an LLM debrief"
    )
    parser.add_argument("--model", "-m", default=DEFAULT_MODEL,
                        help=f"Ollama model to use (default: {DEFAULT_MODEL})")
    parser.add_argument("--hours", "-t", type=int, default=DEFAULT_HOURS,
                        help=f"Hours of activity to collect (default: {DEFAULT_HOURS})")
    parser.add_argument("--dir", "-d", default=DEFAULT_DIR,
                        help=f"Directory to scan (default: home dir)")
    args = parser.parse_args()

    display_banner(args.hours, args.dir)

    # Verify ollama
    console.print()
    with Progress(SpinnerColumn(), TextColumn("[dim]{task.description}"),
                  console=console, transient=True) as prog:
        task = prog.add_task("Connecting to ollama...", total=None)
        try:
            available_models = ollama.list()
            prog.stop()
        except Exception as e:
            prog.stop()
            console.print(f"[bold red]ERROR:[/bold red] Cannot reach ollama.")
            console.print(f"  Run [yellow]ollama serve[/yellow] in a separate terminal.")
            console.print(f"  Details: {e}")
            sys.exit(1)

    # Collect data
    console.print("[dim]Collecting activity data...[/dim]")
    console.print()

    ctx = collect_system_context()

    with Progress(SpinnerColumn(), TextColumn("[dim]{task.description}"),
                  console=console, transient=True) as prog:

        t1 = prog.add_task("Scanning git repositories...", total=None)
        git_data = collect_git_activity(args.dir, args.hours)
        prog.update(t1, description=
            f"[green]✓[/green] Git: {git_data['total_commits']} commits found")
        prog.stop_task(t1)

        t2 = prog.add_task("Finding modified files...", total=None)
        files = collect_modified_files(args.dir, args.hours)
        prog.update(t2, description=
            f"[green]✓[/green] Files: {len(files)} modified files found")
        prog.stop_task(t2)

        t3 = prog.add_task("Reading shell history...", total=None)
        history = collect_shell_history(args.hours)
        prog.update(t3, description=
            f"[green]✓[/green] Shell: {len(history)} commands found")
        prog.stop_task(t3)

    display_data_summary(git_data, files, history, ctx, args.hours)

    # Check we have enough data
    total_signals = git_data["total_commits"] + len(files) + len(history)
    if total_signals == 0:
        console.print(
            "[yellow]Warning:[/yellow] Very little activity data found. "
            "The debrief may be generic.\n"
            "Try: [dim]--dir /path/to/your/projects[/dim]"
        )

    # Format data for LLM
    prompt_data = format_for_prompt(git_data, files, history, ctx, args.hours)

    # Generate debrief
    console.print(Rule("[bold cyan]Generating Debrief[/bold cyan]", style="cyan"))
    console.print()

    t_start  = time.time()
    response = ""

    with Progress(SpinnerColumn(), TextColumn("[dim]{task.description}"),
                  console=console, transient=True) as prog:
        task = prog.add_task(f"Asking {args.model}...", total=None)
        response = generate_debrief(args.model, prompt_data)
        elapsed  = time.time() - t_start
        prog.stop()

    # Parse and display
    sections = parse_debrief(response)
    display_debrief(sections, ctx, args.hours, args.model)

    console.print(
        f"  [dim]⏱  Completed in {elapsed:.1f}s[/dim]"
    )
    console.print()

    # Optionally save to file
    save_path = Path.cwd() / f"debrief_{datetime.now().strftime('%Y%m%d_%H%M')}.txt"
    try:
        with open(save_path, "w", encoding="utf-8") as f:
            f.write(f"DailyDebrief — {ctx['date']}\n")
            f.write(f"Generated: {datetime.now().strftime('%H:%M')} by {args.model}\n")
            f.write("=" * 60 + "\n\n")
            for label, content in sections:
                f.write(f"{label}: {content}\n\n")
        console.print(f"  [dim]Saved to: {save_path}[/dim]")
    except Exception:
        pass   # saving is optional, never fail here

    console.print()


if __name__ == "__main__":
    main()