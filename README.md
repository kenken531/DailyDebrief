# DailyDebrief 📋

DailyDebrief is a **personal productivity flight recorder**: it collects data from your day — git commits, recently modified files, shell history — feeds it all to a local LLM, and outputs a clean 5-section structured debrief directly in your terminal. It's built for the **BUILDCORED ORCAS — Day 13** challenge.

## How it works

- Scans for **git repositories** under your project directory and collects all commits made in the last N hours, including commit messages, authors, and file counts.
- Walks your directory tree to find **recently modified files**, grouped by extension, skipping `node_modules`, `__pycache__`, `.venv`, and other noise directories.
- Reads your **shell history** from PowerShell, bash, or zsh — whichever is available.
- Formats all of this into a structured data block and sends it to a **local LLM via ollama**.
- The LLM produces a **5-section debrief** rendered in your terminal with `rich`: what you built, what broke, what you learned, what's next, and one broader insight.
- The debrief is also **saved to a timestamped `.txt` file** in the current directory.

## Requirements

- Python 3.10.x
- [ollama](https://ollama.com/download) installed and running
- The model pulled locally (see Setup)

## Python packages:

```bash
pip install ollama gitpython rich
```

## Setup

1. Download and install ollama from [ollama.com/download](https://ollama.com/download).
2. In a **separate terminal**, start the ollama server:
```
ollama serve
```
3. Pull the model:
```
ollama pull qwen2.5:3b
```
4. Install the Python packages (see above or run:
```
pip install -r requirements.txt
```
after downloading `requirements.txt`)

## Usage

From the project folder:

```bash
python dailydebrief.py
```

With options:

```bash
python dailydebrief.py --hours 12                        # look back 12 hours
python dailydebrief.py --dir C:/Projects                 # scan a specific folder
python dailydebrief.py --model llama3.2:3b               # use a different model
python dailydebrief.py --hours 4 --dir C:/Projects/myapp # combine options
```

The output looks like this:

```
╭─── 🔨 BUILT ───╮
│ Implemented the data collection pipeline with git and file scanning.
╰────────────────╯
╭─── 🔥 BROKE ───╮
│ The git scanner raised InvalidGitRepositoryError on bare repos.
╰────────────────╯
... and so on
```

## Debrief sections

| Section | What it covers |
|---|---|
| 🔨 BUILT | What you worked on or completed |
| 🔥 BROKE | What failed, errored, or caused friction |
| 📚 LEARNED | A key insight or thing you discovered |
| ⏭ NEXT | The most important next step |
| 💡 INSIGHT | A broader observation or pattern from the day |

## Common fixes

**No git commits found** — make sure `--dir` points to a folder that contains a `.git` directory, or a parent of one. Try `--dir C:/Projects` instead of your home folder.

**Shell history empty** — the script checks `%APPDATA%\Microsoft\Windows\PowerShell\PSReadLine\ConsoleHost_history.txt` for PowerShell history. Make sure PSReadLine is enabled in your PowerShell profile.

**LLM ignores the structure** — the prompt demands the 5 sections by emoji and label. If the model drifts, try a larger model: `--model qwen2.5:7b`.

**ollama not running** — open a separate terminal and run `ollama serve` before launching DailyDebrief.

**gitpython not installed** — git collection is skipped gracefully, but for full results run `pip install gitpython`.

## Hardware concept

DailyDebrief mirrors a **flight data recorder**: every sensor stream (git, files, shell) is sampled, compressed into a structured log, and fed into a summarization pipeline that produces a human-readable report. This is the same pattern used in telemetry systems, observability tools, and embedded logging — collect all signals, aggregate, compress, report. The LLM is the compression stage: it reduces thousands of tokens of raw activity into five sentences of signal.

## Credits

- Local LLM inference: [ollama](https://ollama.com)
- Git data: [GitPython](https://gitpython.readthedocs.io)
- Terminal rendering: [rich](https://github.com/Textualize/rich)
- Default model: [Qwen2.5 3B](https://ollama.com/library/qwen2.5)

Built as part of the **BUILDCORED ORCAS — Day 13: DailyDebrief** challenge.
