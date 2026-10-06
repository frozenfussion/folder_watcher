# Folder Watcher: Specification

An on-prem, agentic document translator. A resident service watches a `source/` folder. When a new document arrives, an agent powered by a **local open-weight model** decides what to do with it: translate it to English and write it to `destination/`, or skip it if it is already English. Nothing leaves the machine.

This file is the single source of truth for the design. `CLAUDE.md` tells you how to work; this file tells you what to build. If the two disagree, stop and ask Aziz.

Status: **Specification v1, not yet implemented.** Anything marked `VERIFY` could not be tested when this spec was written and must be confirmed against the real machine or the installed tool's `--help` output.

---

## 1. Goals and non-goals

### Goals
1. Show students a complete **on-prem agent**: local model, local service, local files, no cloud calls at runtime.
2. The agent is genuinely agentic: the model **chooses tools** (read, skip, translate, write) from a tool list. It is not a hard-coded pipeline.
3. **Modular.** Supporting a new file type later (DOCX, PDF) means adding one reader module and one config entry. The core loop does not change.
4. **Runs as a service started by hand.** Nothing starts at boot.
5. **CPU and GPU.** Use the GPU and offload as much as fits when one is present. Fall back to CPU when not.
6. **Live-toggle config.** Watching can be switched on or off by editing a config file, with no restart.
7. **Teachable.** Logs show each agent step (tool call, arguments, result) in readable form.

### Non-goals (v1)
- No PDF, DOCX, or OCR. Only `.txt` and `.md`. (The architecture must leave room for them.)
- No web UI, no database, no queue server.
- No multi-user or remote access. The model server listens on localhost only.
- No processing of files that already exist in `source/` when the watcher starts.
- No fine-tuning, no RAG.

---

## 2. Environment

| Item | Value |
|---|---|
| Host | Windows with WSL2 (Ubuntu) |
| Project path | `~/projects/folder_watcher` (Linux filesystem, **not** `/mnt/c`) |
| GPU (reference machine) | NVIDIA GeForce RTX 3070 class laptop GPU, 8 GB VRAM, driver reports CUDA 13.1 |
| Init system | systemd, enabled in WSL via `/etc/wsl.conf` |
| Python | 3.11 or newer preferred (`tomllib` is in the standard library from 3.11). If the system Python is older, depend on `tomli` instead and say so in the README. |

The project must also work on a machine **without** a GPU. Never assume CUDA is present.

Keep the project on the Linux filesystem. File-change events from `/mnt/c` are unreliable under WSL2.

---

## 3. Model

| Item | Value |
|---|---|
| Model | Qwen3.5-9B (open weights, Apache-2.0) |
| Format | GGUF, 4-bit |
| File | `Qwen3.5-9B-Q4_K_M.gguf` (about 5.7 GB) |
| Source repo | `unsloth/Qwen3.5-9B-GGUF` on Hugging Face (ungated at time of writing) |
| Vision projector | **Not needed.** Do not download any `mmproj-*.gguf`. This project is text only. |
| Local location | `models/` inside the project, git-ignored |

Alternatives for students with less memory are documented in the README (Qwen3.5-4B, `Qwen3.5-4B-Q4_K_M.gguf`, about 2.7 GB, from `unsloth/Qwen3.5-4B-GGUF`). The model file name and path are config values, so switching models is a config edit plus a service restart, never a code change.

### Qwen3.5 behaviour to respect
Taken from the model card:
- Qwen3.5 **thinks by default**, emitting `<think>...</think>` before the answer. For this project, **disable thinking**: it wastes tokens and time on a translation task and pollutes tool-call output. The model card's way to do this is the chat-template argument `enable_thinking: false` (`chat_template_kwargs`). `VERIFY` how the installed `llama-server` accepts it: either a server flag (look for `--chat-template-kwargs` in `llama-server --help`) or a per-request field. Use whichever works and document it in a comment.
- Qwen3.5 does **not** support the `/think` and `/nothink` soft switches.
- Recommended sampling for non-thinking, general tasks: `temperature=0.7, top_p=0.8, top_k=20, min_p=0.0, presence_penalty=1.5, repetition_penalty=1.0`.
  - `VERIFY` the effect of `presence_penalty=1.5` on translation. It discourages repeating tokens, which can hurt faithful translation of repetitive text. If translations look wrong, lower it (the model card says 0 to 2 is the allowed range) and record the final value in config.
  - Translation calls and tool-calling calls may use different sampling profiles. Put both in config.
- The model card recommends at least 128K context to preserve its thinking ability. That does not apply here (thinking is off) and will not fit in 8 GB VRAM. Use a modest context (default 8192) and chunk documents.

---

## 4. Architecture

Two cooperating processes, each a systemd **user** service.

```
                 +---------------------------+
 you drop a file |  source/  (new file only) |
 ------------->  +-------------+-------------+
                               | file-created event
                               v
 +-------------------------------------------------------------+
 | folder-watcher.service  (Python)                            |
 |                                                             |
 |  Watcher --> Stability check --> Job queue --> Agent loop   |
 |     ^                                             |  ^      |
 |     | re-reads config on every event              |  |      |
 |  config/config.toml                      tool call |  | result
 |                                                   v  |      |
 |                                            Tool registry    |
 |                       read_file | skip_file | translate_text |
 |                       write_translation | (future: PDF...)  |
 +-----------------------------+-------------------------------+
                               | HTTP, OpenAI-compatible, 127.0.0.1
                               v
 +-------------------------------------------------------------+
 | folder-watcher-llm.service  (llama.cpp llama-server)         |
 | Qwen3.5-9B Q4_K_M, GPU layers offloaded when GPU present     |
 +-------------------------------------------------------------+
                               |
                               v
                       destination/  (translated files)
```

### 4.1 Why two services
- The model server is the heavy, slow-to-start part. The watcher is light. Separating them mirrors real on-prem deployments and lets the model server be reused by other tools.
- The watcher declares `Requires=` and `After=` on the model service, so **starting the watcher starts the model server first**, and stopping the model server stops the watcher.
- The watcher also tolerates the model server being slow to become ready: it polls `GET /health` with a timeout before processing its first job and logs clearly while it waits.

### 4.2 Why the model decides, and the code enforces
The model chooses *what to do*. Ordinary code enforces *what is allowed*. These guardrails live in the tools, not in the prompt, and must never be delegated to the model:
- Writes are only possible inside `destination/`. Any path that resolves outside it (including via `..` or symlinks) is rejected.
- Reads are only possible for the file that triggered the job, inside `source/`.
- Only registered tools can be called. Unknown tool names return an error result to the model.
- The loop has a hard step limit (default 8) and a per-job time limit.

---

## 5. Repository layout

```
folder_watcher/
├── CLAUDE.md
├── SPEC.md
├── README.md
├── pyproject.toml
├── .gitignore                  # models/, .venv/, vendor/, state/, *.log
├── config/
│   └── config.toml             # the one config file (see section 6)
├── source/                     # watched folder, kept in git with .gitkeep
│   └── .gitkeep
├── destination/                # output folder, kept in git with .gitkeep
│   └── .gitkeep
├── models/                     # GGUF files, git-ignored
├── vendor/                     # llama.cpp checkout and build, git-ignored
├── state/                      # runtime state (processed-file ledger), git-ignored
├── scripts/
│   ├── system/                 # scripts that need sudo; user runs them (section 10)
│   │   ├── 01_base_packages.sh
│   │   ├── 02_cuda_toolkit_wsl.sh      # only when an NVIDIA GPU is present
│   │   └── README.md
│   ├── build_llama.sh          # no sudo; builds llama.cpp, CUDA or CPU
│   ├── download_model.sh       # no sudo; fetches the GGUF into models/
│   ├── setup_python.sh         # creates .venv and installs the package
│   ├── install_services.sh     # no sudo; writes systemd user units, does NOT enable them
│   ├── start.sh                # systemctl --user start folder-watcher
│   ├── stop.sh                 # stops both services
│   ├── status.sh               # service state, model health, GPU use, config state
│   └── logs.sh                 # journalctl --user -f for both services
├── src/folder_watcher/
│   ├── __main__.py             # CLI: `watch`, `llm-server`, `check`, `config`
│   ├── config.py               # load + validate config
│   ├── watcher.py              # filesystem events, new-files-only, stability check
│   ├── queue.py                # job queue, one worker
│   ├── agent.py                # the tool-calling loop
│   ├── llm_client.py           # thin OpenAI-compatible client
│   ├── prompts.py              # system prompts (agent, translator)
│   ├── chunking.py             # split / rejoin long texts
│   ├── state.py                # processed-file ledger
│   ├── guards.py               # path safety, extension checks
│   ├── launch_llm.py           # builds llama-server argv from config, then execs it
│   └── tools/
│       ├── __init__.py         # registry: discovers and registers tool modules
│       ├── base.py             # Tool dataclass / protocol
│       ├── readers_text.py     # .txt and .md reader tool(s)
│       ├── skip.py             # skip_file
│       ├── translate.py        # translate_text
│       └── write_out.py        # write_translation
└── tests/
    ├── test_guards.py
    ├── test_chunking.py
    ├── test_registry.py
    └── test_watcher_new_only.py
```

The layout is a recommendation. Deviations are fine if the module boundaries in section 8 are kept.

---

## 6. Configuration

One file: `config/config.toml`. Parsed with `tomllib` (or `tomli` on Python < 3.11).

```toml
[watch]
enabled = true                      # LIVE: re-read on every event. false = ignore new files.
source_dir = "source"               # relative paths resolve against the project root
destination_dir = "destination"
extensions = [".txt", ".md"]        # LIVE. Each must have a registered reader tool.
ignore_hidden = true                # skip dotfiles and editor temp files
stable_seconds = 2.0                # file size must be unchanged this long before processing
stable_timeout_seconds = 60.0       # give up waiting for a file to settle after this long

[agent]
english_action = "skip"             # "skip" = do nothing; "copy" = copy untouched to destination
output_suffix = ".en"               # report.md -> report.en.md
max_steps = 8
job_timeout_seconds = 600
chunk_max_chars = 3000              # translation chunk size, tune for the context window

[llm]                               # NOT live. Changing these needs a service restart.
host = "127.0.0.1"
port = 8080
model_path = "models/Qwen3.5-9B-Q4_K_M.gguf"
alias = "qwen3.5-9b"
ctx_size = 8192
gpu_layers = "auto"                 # "auto", "all", "0" (force CPU), or a number. VERIFY accepted values.
parallel = 1
extra_args = []                     # escape hatch for llama-server flags

[llm.sampling.agent]                # used for tool-calling turns
temperature = 0.7
top_p = 0.8
top_k = 20
min_p = 0.0
presence_penalty = 1.5

[llm.sampling.translate]            # used for translation calls
temperature = 0.3
top_p = 0.8
top_k = 20
min_p = 0.0
presence_penalty = 0.0

[logging]
level = "INFO"
trace_agent = true                  # print every tool call and result, for teaching
```

(The `[llm.sampling.translate]` values above are starting points. The model card gives no translation-specific profile. `VERIFY` by testing and tune.)

### 6.1 Live toggle: exact semantics
- The watcher **re-reads the `[watch]` and `[agent]` sections each time a file event arrives**, plus once when the file's modification time changes (so a toggle is logged immediately, not only on the next file). A parse error in the config must **not** crash the watcher: log the error, keep the last good config.
- `watch.enabled = false`: the service keeps running and logs `watching disabled` once on the change. New files that arrive while disabled are **ignored permanently**. They are not queued and are **not** processed when watching is re-enabled. This matches "new arrivals only" and avoids surprise backfills.
- `watch.enabled = true`: new files are processed again from that moment.
- The `[llm]` section is read only at model-server start. Changing it requires `systemctl --user restart folder-watcher-llm`.
- Startup validation: every extension in `watch.extensions` must have a registered reader tool. If not, log a clear error naming the extension and refuse to process files of that type (do not crash).

---

## 7. Behaviour

### 7.1 New files only
- At service start, the watcher records nothing and processes nothing from files already in `source/`.
- It reacts to files that **appear after the watcher started**: `created` events and `moved-into` events (a file moved or renamed into `source/`).
- Use the `watchdog` library (inotify backend on Linux). Handle both event types, because copy tools and editors behave differently.
- Do **not** treat `modified` events on an already-seen file as new arrivals.
- A small ledger in `state/` (path + size + mtime, or a content hash) prevents the same file from being processed twice if duplicate events fire. The ledger is a de-duplication aid, not a backlog.

### 7.2 Stability check
A copy in progress can emit a `created` event while the file is incomplete. Before queuing a job:
1. Ignore the file if it is hidden or a temp file (leading `.`, trailing `~`, `.swp`, `.tmp`, `.part`, `.crdownload`).
2. Ignore it if its extension is not in `watch.extensions` (log at DEBUG, not as an error).
3. Poll the file size until it is unchanged for `stable_seconds`. Give up after `stable_timeout_seconds` and log a warning.
4. Check the file is readable and non-empty. Empty files are logged and skipped.

### 7.3 Job processing
One worker, one job at a time (the GPU is the bottleneck, and 8 GB does not allow parallel slots). Jobs are queued FIFO. A failed job never stops the queue.

### 7.4 The agent loop
For each job the agent receives a short task message, for example:

> A new file has arrived: `source/report.md`. Decide what to do with it, using the tools.

Loop:
1. Send the conversation plus the **tool schemas** (OpenAI function-calling format) to the model server.
2. If the model returns tool calls, execute each through the registry, append the results, and loop.
3. If the model returns plain text with no tool call, the job is over. Log the text as the final message.
4. Stop at `max_steps` or `job_timeout_seconds`. Log the job as failed.

The agent's system prompt (in `prompts.py`) must say, in plain words:
- Your job is to turn foreign-language documents into English files.
- First read the file. If the document is already English, call `skip_file` with a short reason. Do not translate it.
- Otherwise call `translate_text`, then `write_translation` with the result.
- Never write anywhere except through `write_translation`.
- Mixed documents: translate if the **majority** of the text is not English.
- Always finish with exactly one of `skip_file` or `write_translation`.

`VERIFY` early that Qwen3.5-9B Q4 follows this reliably through `llama-server`'s tool-calling support (`--jinja` is needed for tool calls; confirm in `--help`). If tool calling is flaky, improve the prompt and tool descriptions first. Do not silently hard-code the decision in Python, because the lesson is the model choosing. If a deterministic safety net is added (for example "if the loop ended with no terminal tool, log failure"), keep it visible in the logs.

### 7.5 Tools (v1)

Every tool has: a `name`, a plain-language `description` (the model reads this), a JSON-schema `parameters` block, a handler, and optionally the file `extensions` it applies to.

| Tool | Parameters | Returns | Notes |
|---|---|---|---|
| `read_file` | `path` | `{ "text_preview": "...", "total_chars": N, "format": ".md" }` | Reader for `.txt` and `.md`. Preview is the first ~1500 characters, enough to judge the language. Full text stays server-side and is handed to `translate_text` by reference, never pushed through the model's context. Enforces that `path` is the job's file. |
| `skip_file` | `reason` | `{ "status": "skipped" }` | Terminal tool. Behaviour depends on `agent.english_action`: `skip` does nothing, `copy` copies the file untouched to `destination/`. Records the job as done in the ledger. |
| `translate_text` | `source_language` (best guess, optional) | `{ "translation_id": "...", "chunks": N }` | Reads the full text from the job, chunks it, translates each chunk with a separate model call using the translator prompt and `llm.sampling.translate`, and stores the joined result in job memory under `translation_id`. Returns a summary, not the full text. |
| `write_translation` | `translation_id`, optional `filename` | `{ "status": "written", "path": "destination/report.en.md" }` | Terminal tool. Writes the stored translation into `destination/`. Name defaults to `<stem><output_suffix><ext>`. If the name exists, append `-1`, `-2`, and so on. Never overwrites. Enforces the destination guard. |

Design note: keeping the large texts out of the tool-calling conversation (hence `translation_id`) is deliberate. A 9B model with an 8K context cannot afford to carry a whole document through the chat history.

### 7.6 Translator prompt (used inside `translate_text`)
Requirements for the prompt in `prompts.py`:
- Translate to English. Output **only** the translation, with no preamble, no notes, and no `<think>` text.
- Preserve structure: Markdown headings, lists, tables, emphasis, blank lines, and line breaks.
- **Do not translate** fenced code blocks, inline code, URLs, file paths, or HTML tags. Translate comments only if the block is clearly prose.
- Keep proper nouns as they are, unless they have an established English form.
- If a chunk is already English, return it unchanged.

### 7.7 Chunking (`chunking.py`)
- Split on blank lines first, then on single newlines, then on sentence boundaries, to stay under `agent.chunk_max_chars`.
- Never split inside a fenced code block.
- Keep headings attached to the paragraph that follows them.
- Rejoin chunks in order, preserving the original separators.
- Unit-tested: `join(chunk(text)) == text` for text that needs no translation.

### 7.8 Output files
- Output name: `<stem><output_suffix><ext>`, for example `report.md` becomes `report.en.md`.
- Encoding: read as UTF-8, with a fallback attempt (for example `utf-8-sig`, then `latin-1`) logged as a warning. Write as UTF-8.
- Never delete or move the original from `source/`.

### 7.9 Failure handling
- Model server unreachable: retry with backoff, log clearly, and keep the job queued. Do not drop it.
- Tool error: return the error text to the model as the tool result so it can react, count it as a step.
- Job fails (step limit, timeout, repeated errors): log `FAILED` with the reason. Write nothing to `destination/`. Record in the ledger so the file is not retried endlessly.
- Any exception in a job is caught at the job boundary. The service stays up.

---

## 8. Modularity contract

Tools are plug-ins. Adding a format must not require editing the agent loop.

### 8.1 Tool interface (`tools/base.py`)
```python
@dataclass
class Tool:
    name: str
    description: str                 # shown to the model
    parameters: dict                 # JSON schema
    handler: Callable[[dict, JobContext], dict]
    extensions: tuple[str, ...] = () # file types this tool serves; empty = general tool
    terminal: bool = False           # True for skip_file / write_translation
```
`JobContext` carries: the job's source path, the loaded text, the translation store, the destination guard, the LLM client, and the current config snapshot.

### 8.2 Registry (`tools/__init__.py`)
- Auto-discovers modules in `tools/` that expose a `TOOLS: list[Tool]` attribute.
- `registry.for_extension(".md")` returns the reader tool(s) that serve that extension.
- `registry.schemas_for_job(path)` returns only the tools relevant to the file: the matching reader plus all general tools. The model never sees PDF tools when handling a `.txt`.
- `registry.supported_extensions()` is what config validation checks against.

### 8.3 Adding a format later (documented example, do not implement now)
To add DOCX: create `tools/readers_docx.py` exposing a `read_docx` tool with `extensions=(".docx",)`, add its dependency to `pyproject.toml` as an optional extra, and add `".docx"` to `watch.extensions`. Nothing else changes. The README should show this example under "Extending".

---

## 9. Model server (`launch_llm.py`)

`python -m folder_watcher llm-server` reads `config/config.toml`, builds the `llama-server` command line, then `exec`s it, so systemd supervises `llama-server` directly. This keeps one source of truth for model settings.

Command line built from config (`VERIFY` every flag name and value with `vendor/llama.cpp/build/bin/llama-server --help`, since flags change between releases):
- `-m <model_path>`
- `--host <host> --port <port>`: **host must default to `127.0.0.1`**, never `0.0.0.0`.
- `-c <ctx_size>`
- `-ngl <gpu_layers>`: `auto` or `all` per the llama.cpp server README. When no GPU backend was compiled in, this must be harmless or omitted.
- `--jinja`: needed for tool calling and chat templates.
- `--alias <alias>`
- `-np <parallel>`
- the thinking-disable setting from section 3
- `llm.extra_args`

The server README also lists `--fit` ("automatically adjusts settings to fit device memory"). `VERIFY` its syntax and enable it if it behaves well, since it helps on small GPUs and unknown machines.

Health: `GET /health` returns ready when the model is loaded. Chat endpoint: `POST /v1/chat/completions`.

### 9.1 CPU or GPU selection
**Build time** (`scripts/build_llama.sh`):
1. If `nvidia-smi` works **and** `nvcc` is found (check `PATH` and `/usr/local/cuda/bin/nvcc`), build with `cmake -B build -DGGML_CUDA=ON` then `cmake --build build --config Release -j <cores>`.
2. If an NVIDIA GPU is present but `nvcc` is missing, **do not silently fall back to CPU**. Stop and tell Aziz that `scripts/system/02_cuda_toolkit_wsl.sh` needs to be run, with the exact command. (A CPU build on a GPU machine is a surprising 10x slowdown, so ask first.)
3. If no GPU: CPU build with `cmake -B build`.
4. Support an override: `./scripts/build_llama.sh --cpu` forces a CPU build.
5. Print which backend was built and save it to `vendor/BACKEND` (`cuda` or `cpu`) so `status.sh` can show it.

llama.cpp's build documentation states the CUDA toolkit is a prerequisite, and it lists no prebuilt Linux CUDA binaries. So **build from source**, from the latest release or `master`: Qwen3.5 is a recent architecture and older llama.cpp builds may not load it. If loading fails, update llama.cpp first before anything else.

**Run time**: `gpu_layers = "auto"` offloads as many layers as fit. On a CPU-only build the flag has no effect. `gpu_layers = "0"` forces CPU even on a GPU machine.

### 9.2 Memory budget (reference machine: 8 GB VRAM)
- Q4_K_M weights are about 5.7 GB. The desktop/WSL display stack was already using roughly 0.6 GB in the reference `nvidia-smi` output, leaving about 7.5 GB.
- The KV cache grows with `ctx_size`. Start at 8192. If the server fails to start with an out-of-memory error, reduce `ctx_size` first, then fall back to the 4B model (README documents this).
- If a student's machine has less memory than the model needs, `gpu_layers = "auto"` will leave layers on the CPU. It will work, but slower.

---

## 10. Installation hurdles and the `sudo` rule

Claude Code running on Aziz's machine **cannot enter a sudo password**. Therefore:

1. Do everything that does not need root yourself: create the venv, build llama.cpp, download the model, write user-level systemd units.
2. For anything that needs root, **do not attempt it and do not loop**. Write a small, readable, idempotent script under `scripts/system/`, with comments explaining each step, then tell Aziz the exact command to run (for example `sudo bash scripts/system/01_base_packages.sh`). Wait for him to confirm before continuing.
3. Every script must be safe to run twice.
4. PATH problems must be solved **in the current shell**, never by telling Aziz to close the terminal. Scripts use absolute paths where possible. When an environment change is unavoidable, give the `export ...` line to run now and also persist it in `~/.bashrc`.

### 10.1 `scripts/system/01_base_packages.sh`
Installs build prerequisites with `apt`: at least `build-essential`, `cmake`, `git`, `curl`, `python3-venv`, `python3-pip`, `pkg-config`, and `libcurl4-openssl-dev` if the llama.cpp build asks for it (`VERIFY`). Runs `apt update` first.

### 10.2 `scripts/system/02_cuda_toolkit_wsl.sh`
Only for machines with an NVIDIA GPU. Installs the **CUDA toolkit** inside WSL. Critical, from NVIDIA's WSL user guide:
- **Never install an NVIDIA Linux driver inside WSL2.** The Windows driver is already exposed in WSL as `libcuda.so`, and a normal CUDA package can overwrite it.
- Use NVIDIA's **WSL-Ubuntu** CUDA toolkit packages. If using the standard Ubuntu repository, install only a `cuda-toolkit-<major>-<minor>` package. **Never** install `cuda`, `cuda-<ver>`, or `cuda-drivers`.
- Pick a toolkit version that the installed Windows driver supports (the reference machine reports CUDA 13.1 in `nvidia-smi`; the toolkit must not be newer than that).
- Afterwards, make `nvcc` reachable: `/usr/local/cuda/bin` on `PATH`. Check with `nvcc --version`.
- `VERIFY` the exact repository setup and package names on NVIDIA's current "CUDA Toolkit Downloads" page for Linux, x86_64, WSL-Ubuntu. Do not guess package names. Fetch the page and follow it.

### 10.3 Other hurdles Claude Code should handle by itself
- Python venv or `pip` problems: fix in `.venv`, never with `sudo pip`.
- Missing `python3-venv`: that is a `sudo` item, so put it in `01_base_packages.sh`.
- Model download failure: retry, then report. If the repository ever turns out to be gated, tell Aziz and ask him to run `hf auth login` (or equivalent) himself. Never ask him to paste a token into the chat.
- Port 8080 already in use: pick another port in config and say so.

---

## 11. systemd user services

Installed by `scripts/install_services.sh` into `~/.config/systemd/user/`, followed by `systemctl --user daemon-reload`. **Never run `systemctl --user enable`**: the units must not start at login or boot. Starting is always manual.

`folder-watcher-llm.service`
```ini
[Unit]
Description=Folder Watcher: local LLM server (llama.cpp)

[Service]
Type=simple
WorkingDirectory=<project root>
ExecStart=<project root>/.venv/bin/python -m folder_watcher llm-server
Restart=on-failure
RestartSec=3
# Loading the model can take a while.
TimeoutStartSec=300
```

`folder-watcher.service`
```ini
[Unit]
Description=Folder Watcher: agent that translates new documents
Requires=folder-watcher-llm.service
After=folder-watcher-llm.service

[Service]
Type=simple
WorkingDirectory=<project root>
ExecStart=<project root>/.venv/bin/python -m folder_watcher watch
Restart=on-failure
RestartSec=3
```

Notes:
- No `[Install]` section, on purpose. That is what keeps the units from being enable-able by accident.
- `<project root>` is substituted by the install script with the absolute path.
- Manual control: `systemctl --user start folder-watcher` (also starts the LLM), `systemctl --user stop folder-watcher folder-watcher-llm`, `journalctl --user -u folder-watcher -u folder-watcher-llm -f`.
- `Requires=` means stopping the LLM service also stops the watcher. Stopping only the watcher leaves the model server loaded (and VRAM held). `stop.sh` stops both.
- WSL needs systemd enabled (`[boot] systemd=true` in `/etc/wsl.conf`, then `wsl.exe --shutdown` from Windows). Per Microsoft's documentation this setting is available on Windows 11 and Windows Server 2022, and needs a recent Store version of WSL (`wsl --version`). `scripts/status.sh` or a `check` command must detect when systemd is not running and print the fix, not a stack trace.
- `VERIFY` that user services behave under WSL (a user session must be active). If `systemctl --user` fails with "Failed to connect to bus", diagnose and document the fix in the README.

---

## 12. CLI (`python -m folder_watcher ...`)

| Command | Purpose |
|---|---|
| `watch` | Run the watcher (what the service runs). |
| `llm-server` | Build the `llama-server` command from config and exec it. |
| `check` | Preflight: config valid, extensions have readers, directories exist, model file present, `llama-server` binary present, systemd running, GPU/backend detected, server health if running. Exits non-zero on failure with plain-language fixes. |
| `config get|set <key> [value]` | Read or edit a config value, for example `config set watch.enabled false`. A convenient live-toggle for the demo. Preserves comments if feasible; otherwise document that it rewrites the file. |

`scripts/status.sh` wraps `check` and also shows `systemctl --user` state for both units, the backend from `vendor/BACKEND`, and `nvidia-smi` memory use when available.

---

## 12a. Dependencies

Keep them minimal and pinned by lower bound in `pyproject.toml`.
- `watchdog`: filesystem events.
- `openai`: client for the OpenAI-compatible endpoint (point `base_url` at the local server; the API key is a dummy).
- `tomli` only if Python < 3.11.
- `pytest` as a dev extra.
- Optional extras reserved for future readers (`docx`, `pdf`): declare the extras group, leave it empty in v1.

Do not add heavyweight agent frameworks. The loop is small on purpose, and students should be able to read it in one sitting.

---

## 13. Logging

- All logs go to stdout and are captured by journald.
- Each job gets a short id. Every line for that job carries it.
- With `logging.trace_agent = true`, log each step as, for example:
  ```
  [job 7f3a] step 1 -> tool read_file {"path": "source/report.md"}
  [job 7f3a] step 1 <- {"text_preview": "Bonjour tout le monde ...", "total_chars": 4210}
  [job 7f3a] step 2 -> tool translate_text {"source_language": "French"}
  [job 7f3a] step 2 <- {"translation_id": "t1", "chunks": 2}
  [job 7f3a] step 3 -> tool write_translation {"translation_id": "t1"}
  [job 7f3a] step 3 <- {"status": "written", "path": "destination/report.en.md"}
  [job 7f3a] DONE in 41.2s
  ```
- Never log full document contents above DEBUG level (on-prem demos still handle sensitive documents).

---

## 14. Tests

Unit tests that need **no model**:
- `guards`: reject `../`, absolute paths outside the folders, and symlink escapes.
- `chunking`: round-trip, no split inside code fences, headings stay attached.
- `registry`: extension to reader lookup, schema filtering per file, validation of configured extensions.
- `watcher` (new-files-only): files present at startup are ignored, a file created afterwards is picked up, a file moved into `source/` is picked up, and a file still growing is not queued until it stabilises.
- `config`: live reload picks up `enabled` changes, and a malformed file keeps the last good config.

Integration test, **model required**, marked so it is skipped by default and run by hand:
- Drop an English `.txt` and a non-English `.md`, check the English one is skipped, the other produces a `.en.md` in `destination/`, and structure (headings, code blocks) survives.

Report test results honestly. If a test cannot run on the machine, say why.

---

## 15. Acceptance criteria

The build is done when all of these hold on Aziz's machine, demonstrated, not assumed:

1. `scripts/status.sh` runs and reports system state sensibly, with services stopped.
2. `scripts/start.sh` starts both services. The model loads on the GPU, and the log or `nvidia-smi` shows offload. The services do **not** start by themselves after `wsl --shutdown` and reopening.
3. With `source/` empty, dropping a French (or any non-English) `.txt` produces a correct English `*.en.txt` in `destination/`.
4. Dropping an English `.md` produces **no** output (with the default `english_action = "skip"`), and the log shows the agent's reason for skipping.
5. A Markdown file with headings, a list, and a fenced code block translates with structure intact and the code block untouched.
6. Setting `watch.enabled = false` stops processing without a restart. Setting it back to `true` resumes. A file dropped while disabled is **not** processed after re-enabling.
7. Files already in `source/` at service start are not processed.
8. A `.pdf` dropped into `source/` is ignored with a clear log line, and the service stays up.
9. With `llm.gpu_layers = "0"` (or on a CPU build) the pipeline still works, only slower.
10. `stop.sh` stops both services and frees the GPU memory.
11. Unit tests pass.
12. Every sudo step is covered by a script in `scripts/system/`, with exact run instructions printed.

---

## 16. Open questions for Aziz during the build

Ask only when blocked; otherwise choose the default and record it in the README.
- If Python on the machine is older than 3.11, accept adding `tomli`?
- If tool calling proves unreliable with the 9B Q4 model on the installed llama.cpp, does he prefer prompt tuning, or a different Qwen3.5 quant (for example Q5_K_M, about 6.6 GB, if VRAM allows)?

---

## 17. Reference facts used in this spec

Each was read from a source on the date of writing (2026-10-06). Re-check anything that fails.
- Qwen3.5-9B GGUF files and sizes: `hf.co/unsloth/Qwen3.5-9B-GGUF`. Q4_K_M is 5,680,522,464 bytes.
- Qwen3.5-4B GGUF: `hf.co/unsloth/Qwen3.5-4B-GGUF`. Q4_K_M is 2,740,937,888 bytes. Q5_K_M is 3,143,656,608 bytes.
- Qwen3.5 model card (unsloth mirror of Qwen/Qwen3.5-9B): thinking on by default, `enable_thinking: false` via chat template kwargs, sampling recommendations, no `/think` switches.
- llama.cpp `docs/build.md`: CUDA toolkit required, `-DGGML_CUDA=ON`, no prebuilt Linux CUDA binaries listed.
- llama.cpp `tools/server/README.md`: `-m`, `--host` (default 127.0.0.1), `--port` (default 8080), `-c`, `-ngl` (accepts `auto` or `all`), `--jinja`, `--alias`, `-np`, `--fit`, `/health`, `/v1/chat/completions`, function calling.
- NVIDIA CUDA on WSL user guide: never install an NVIDIA Linux driver inside WSL2; use WSL-Ubuntu toolkit packages; avoid `cuda`, `cuda-<ver>`, `cuda-drivers`.
- Microsoft WSL docs: `[boot] systemd=true` in `/etc/wsl.conf`, restart with `wsl.exe --shutdown`; the `[boot]` section is documented as available on Windows 11 and Server 2022; check version with `wsl --version`.
