# Folder Watcher: an on-prem AI agent

Drop a document into a folder. A small AI agent, running **entirely on your own machine** with an open-weight model, notices it, decides what to do, and (if the document is not in English) writes an English translation to another folder. No cloud, no API keys, no data leaving your computer.

> **Status: Milestones 1 to 3 of 7 are built** (Python package and config, system prerequisites, local model server). You can test the model server today: see [Testing it](#testing-it). The watcher, the agent and the services are **not built yet**, so the "Using it" section still describes *planned* behaviour. The full design is in [`SPEC.md`](SPEC.md).

---

## What it does

1. A service watches `source/`.
2. A **new** file arrives (files already there at start-up are ignored).
3. The agent reads the file and decides:
   - already English: **skip it**, and log why
   - not English: **translate** it and write `destination/<name>.en.<ext>`
4. You can switch watching on or off at any time by editing one config file. No restart needed.

Version 1 handles `.txt` and `.md`. The design is modular, so other formats (DOCX, PDF) are added later as extra tools.

## The agent, at a glance

### Components

```mermaid
flowchart LR
    U([You drop a file]) --> S[/source/ folder/]

    subgraph WATCHER["folder-watcher.service (Python)"]
        direction TB
        W[Watcher<br/>new files only] --> ST[Stability check<br/>file finished copying?]
        ST --> Q[Job queue]
        Q --> A[Agent loop]
        A <--> R[Tool registry]
        CFG[(config/config.toml<br/>live toggle)] -. read on every event .-> W
    end

    S -- file-created event --> W

    subgraph TOOLS["Tools (modular)"]
        direction TB
        T1[read_file<br/>.txt .md]
        T2[skip_file]
        T3[translate_text]
        T4[write_translation]
        T5[future: PDF, DOCX ...]:::future
    end

    R --- TOOLS

    subgraph LLM["folder-watcher-llm.service"]
        M[llama.cpp llama-server<br/>Qwen3.5-9B, 4-bit<br/>GPU if available, else CPU]
    end

    A -- "chat + tool calls<br/>HTTP, localhost only" --> M
    T3 -- "translate chunks" --> M
    T4 --> D[/destination/ folder/]

    classDef future stroke-dasharray: 5 5,fill:#f6f6f6,color:#777;
```

### What the agent decides for each file

```mermaid
flowchart TD
    N([New file in source/]) --> E{Enabled in config?<br/>Extension supported?}
    E -- no --> IGN([Ignored, logged])
    E -- yes --> STB[Wait until file is stable]
    STB --> RD[Agent calls read_file<br/>gets a preview]
    RD --> EN{Is it already English?<br/>the model decides}
    EN -- yes --> SK[Agent calls skip_file<br/>with a reason]
    SK --> DONE1([Done, nothing written])
    EN -- no --> TR[Agent calls translate_text<br/>split into chunks, translate each]
    TR --> WR[Agent calls write_translation]
    WR --> OUT[/destination/name.en.ext/]
    OUT --> DONE2([Done])
```

**Who decides what:** the *model* decides which tool to call. Ordinary *code* enforces the rules: it can only write inside `destination/`, only registered tools exist, and there is a hard step limit. That split (the model decides, the code guards) is the core idea of the lesson.

### Two services, started by hand

| Service | What it runs | Notes |
|---|---|---|
| `folder-watcher-llm` | the local model server (llama.cpp) | Loads the model into GPU memory. Slow to start, heavy. |
| `folder-watcher` | the Python watcher and agent | Starting it starts the model server first. |

Neither starts at boot or login. You start them when you want them.

---

## Requirements

- Windows 10 or 11 with **WSL2** and an **Ubuntu** distribution
- (Optional, recommended) an NVIDIA GPU with the **NVIDIA driver installed on Windows**. Without a GPU it still works on CPU, only slower.
- About **6 GB of free disk space** for the project (measured: model 5.3 GiB, llama.cpp build about 0.5 GB, Python environment about 0.1 GB), **plus about 5 GB** for the CUDA toolkit if you have an NVIDIA GPU.
- A **Claude** account that includes Claude Code (Pro, Max, Team, Enterprise, or Console), because Claude Code does the building
- A GitHub account

Model used: **Qwen3.5-9B**, 4-bit (`Q4_K_M`, about 5.7 GB), from [unsloth/Qwen3.5-9B-GGUF](https://hf.co/unsloth/Qwen3.5-9B-GGUF). Apache-2.0 licence, no login needed.

Not enough memory for it? See [Using a smaller model](#using-a-smaller-model).

---

## Setup, from a clean machine

Do the steps in order. Everything marked **PowerShell** runs in Windows PowerShell. Everything else runs in your **Ubuntu (WSL) terminal**.

### Step 1: WSL2 and Ubuntu (Windows side)

If you do not have WSL yet, in **PowerShell**:

```powershell
wsl --install
```

Reboot if Windows asks you to. Then check it is version 2 and your Ubuntu is listed:

```powershell
wsl --list --verbose
wsl --version
```

If `wsl --version` is not recognised, your WSL is an old built-in version. Update it from the Microsoft Store ([aka.ms/wslstorepage](https://aka.ms/wslstorepage)).

Open the **Ubuntu** app from the Start menu. From here on, commands run in that terminal.

### Step 2: NVIDIA driver (only if you have an NVIDIA GPU)

Install or update the normal **NVIDIA driver on Windows**. Then, inside Ubuntu, check the GPU is visible:

```bash
nvidia-smi
```

You should see your GPU in a table. **Do not install any NVIDIA driver inside WSL.** The Windows driver is shared with WSL automatically, and installing a Linux driver there can break it.

No GPU or no output? Carry on. The project falls back to CPU.

### Step 3: Turn on systemd in WSL

The two services run under systemd. First check whether it is already on:

```bash
ps -p 1 -o comm=
```

- It prints `systemd`: it is already on. Skip to Step 4.
- It prints `init` or something else: turn it on.

```bash
sudo tee /etc/wsl.conf > /dev/null <<'EOF'
[boot]
systemd=true
EOF
```

> If you already have a `/etc/wsl.conf` with other settings, do not overwrite it. Open it with `sudo nano /etc/wsl.conf` and add the two `[boot]` lines instead.

Now restart WSL. This is the **one** time you need to restart. In **PowerShell**:

```powershell
wsl.exe --shutdown
```

Then open the Ubuntu app again and confirm:

```bash
ps -p 1 -o comm=
```

It should now print `systemd`. (Microsoft documents the `[boot]` setting for Windows 11 and Windows Server 2022.)

### Step 4: Basic tools

```bash
sudo apt update
sudo apt install -y curl git wget
```

### Step 5: GitHub CLI (`gh`) and sign in

Install it (this is GitHub's official method for Debian and Ubuntu):

```bash
(type -p wget >/dev/null || (sudo apt update && sudo apt install wget -y)) \
	&& sudo mkdir -p -m 755 /etc/apt/keyrings \
	&& out=$(mktemp) && wget -nv -O$out https://cli.github.com/packages/githubcli-archive-keyring.gpg \
	&& cat $out | sudo tee /etc/apt/keyrings/githubcli-archive-keyring.gpg > /dev/null \
	&& sudo chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
	&& sudo mkdir -p -m 755 /etc/apt/sources.list.d \
	&& echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | sudo tee /etc/apt/sources.list.d/github-cli.list > /dev/null \
	&& sudo apt update \
	&& sudo apt install gh -y
```

Check it:

```bash
gh --version
```

Sign in. Choose **GitHub.com**, then **HTTPS**, then **Login with a web browser**, and follow the prompts:

```bash
gh auth login
```

Let git use that sign-in, and confirm it worked:

```bash
gh auth setup-git
gh auth status
```

Tell git who you are (use your own name and the email on your GitHub account):

```bash
git config --global user.name "Your Name"
git config --global user.email "you@example.com"
```

### Step 6: Install Claude Code

Use the official native installer:

```bash
curl -fsSL https://claude.ai/install.sh | bash
```

The installer puts `claude` in `~/.local/bin`. Make that folder available **right now**, in this same terminal (no need to close it), and make it permanent:

```bash
export PATH="$HOME/.local/bin:$PATH"
grep -qxF 'export PATH="$HOME/.local/bin:$PATH"' ~/.bashrc || echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
```

Check it:

```bash
claude --version
claude doctor
```

`claude --version` should print a version number. `claude doctor` prints a health check of the installation.

If `claude --version` says "command not found", run `ls -la ~/.local/bin/claude`. If that file exists, the `export` line above did not run in this terminal, so run it again. If the file does not exist, the install did not finish: re-run the installer and read its output.

Install and run Claude Code **inside Ubuntu (WSL)**, not from PowerShell.

Sign in the first time you start it (next step). It opens a browser page to log in. If no browser opens, copy the link it shows into a browser on Windows.

### Step 7: Get the project

If you do not have a `~/projects/folder_watcher` folder yet:

```bash
mkdir -p ~/projects
gh repo clone frozenfussion/folder_watcher ~/projects/folder_watcher
cd ~/projects/folder_watcher
```

If you **already** made the folder (for example with empty `source` and `destination` folders inside), connect it to the repository instead:

```bash
cd ~/projects/folder_watcher
git init -b main
git remote add origin https://github.com/frozenfussion/folder_watcher.git
git pull origin main
```

Check you can see the plan files:

```bash
ls
```

You should see `CLAUDE.md`, `SPEC.md`, and `README.md`.

> Keep the project inside your Linux home folder (`~/projects/...`), **not** under `/mnt/c/...`. File-change detection does not work reliably on Windows drives from WSL.

### Step 8: Let Claude Code build it

Start Claude Code in the project folder:

```bash
claude
```

Log in when asked. It reads `CLAUDE.md` automatically. Then paste this prompt:

```text
Read CLAUDE.md and SPEC.md completely, then build the Folder Watcher exactly as
specified. Follow the build order in CLAUDE.md and check in with me after each
milestone. Detect whether this machine has an NVIDIA GPU and build for GPU or
CPU accordingly. When something needs sudo, write a script in scripts/system/
and tell me the exact command to run. Do not guess flags or package names:
verify them first.
```

What to expect while it builds:

- Claude Code will ask permission before running commands. Read each one before you approve.
- When it needs administrator rights, it **cannot** type your password. It writes a script and tells you the command to run, such as `sudo bash scripts/system/01_base_packages.sh`. Run it in a **second** terminal tab (or exit Claude Code, run it, then restart `claude`), then tell Claude Code it is done.
- Building llama.cpp with GPU support and downloading the model take a while. That is normal.

---

## Testing it

These tests cover what is built **today** (Milestones 1 to 3): the Python package, the config file, the llama.cpp build, the downloaded model, and the local model server. Every command below was run on the reference machine (RTX 3070 Ti Laptop GPU, 8 GB) exactly as written.

The model is a language model, so its wording changes from run to run. The steps below tell you what to **check**. Real output is shown only as an *example*.

### Part A: checks that do not need the model server

**A1. Go to the project folder.**

```bash
cd ~/projects/folder_watcher
```

**A2. Run the unit tests.** They test the config loader and the model-server command line, and need no model.

```bash
.venv/bin/pytest -q
```

What you should see: a final line like `12 passed`, and no `failed`.

**A3. Check which backend llama.cpp was built for.**

```bash
cat vendor/BACKEND
```

What you should see: `cuda` on a machine with an NVIDIA GPU, `cpu` otherwise.

**A4. Check the model file is there and complete.**

```bash
ls -l models/
```

What you should see: `Qwen3.5-9B-Q4_K_M.gguf` with a size of exactly `5680522464` bytes.

**A5. Read a value from the config file.**

```bash
.venv/bin/python -m folder_watcher config get watch.enabled
```

What you should see: `True`.

**A6. (GPU only) Note how much GPU memory is in use before the model loads.**

```bash
nvidia-smi --query-gpu=memory.used,memory.total --format=csv
```

What you should see: a small "used" number. Write it down; you compare against it later. Example:

```text
memory.used [MiB], memory.total [MiB]
791 MiB, 8192 MiB
```

### Part B: the local model server

You need **two terminal tabs**. Terminal 1 runs the model server. Terminal 2 sends it requests.

**B1. Terminal 1: go to the project folder.**

```bash
cd ~/projects/folder_watcher
```

**B2. Terminal 1: start the model server.** It reads `config/config.toml`, builds the `llama-server` command, and runs it in this terminal.

```bash
.venv/bin/python -m folder_watcher llm-server
```

What you should see: the first line starts with `backend=cuda exec:` (or `backend=cpu`) followed by the full command. Look for `--host 127.0.0.1`: the server is only reachable from this machine. After a few seconds the last lines include `model loaded` and `listening on http://127.0.0.1:8080`. **Leave it running.**

**B3. Terminal 2: go to the project folder.**

```bash
cd ~/projects/folder_watcher
```

**B4. Terminal 2: ask the server if it is ready.**

```bash
curl -s http://127.0.0.1:8080/health
```

What you should see: `{"status":"ok"}`. If you see `"Loading model"` with code `503` instead, the model is still loading: wait a few seconds and run it again.

**B5. (GPU only) Check the model is in GPU memory.**

```bash
nvidia-smi --query-gpu=memory.used,memory.total --format=csv
```

What you should see: "used" is about **5.5 GB higher** than the number you wrote down in A6. Example: `6251 MiB, 8192 MiB`.

**B6. Ask the model a question.**

```bash
curl -s http://127.0.0.1:8080/v1/chat/completions -H "Content-Type: application/json" -d '{"messages":[{"role":"user","content":"In one sentence, what is the capital of Malaysia?"}],"max_tokens":100}' | python3 -c 'import sys,json; m=json.load(sys.stdin)["choices"][0]["message"]; print("Answer:", m["content"]); print("Thinking text present:", "<think>" in m["content"] or bool(m.get("reasoning_content")))'
```

What you should check: the answer mentions **Kuala Lumpur**, and `Thinking text present: False`. (Qwen3.5 "thinks" out loud by default; the server is started with thinking turned off, because it wastes time on translation.) Example:

```text
Answer: The capital of Malaysia is Kuala Lumpur.
Thinking text present: False
```

**B7. Check the model can call a tool.** The agent (Milestone 4) depends on this. The request offers the model one made-up tool, `get_weather`.

```bash
curl -s http://127.0.0.1:8080/v1/chat/completions -H "Content-Type: application/json" -d '{"messages":[{"role":"user","content":"What is the weather in Penang?"}],"tools":[{"type":"function","function":{"name":"get_weather","description":"Get the current weather for a city.","parameters":{"type":"object","properties":{"city":{"type":"string"}},"required":["city"]}}}],"max_tokens":200}' | python3 -c 'import sys,json; c=json.load(sys.stdin)["choices"][0]; print("finish_reason:", c["finish_reason"]); [print("tool call:", t["function"]["name"], t["function"]["arguments"]) for t in c["message"].get("tool_calls") or []]'
```

What you should check: `finish_reason: tool_calls`, and a tool call to `get_weather` with the city `Penang`. Example:

```text
finish_reason: tool_calls
tool call: get_weather {"city":"Penang"}
```

**B8. Translate the Malay sample document.** The file `tests/samples/laporan_mingguan.md` is a short weekly report in Malay with headings, lists, a code block and a link. The translation tool and agent do not exist yet, so this step sends the file straight to the model server, using the translation settings from `config/config.toml`. The result goes to `state/` (a scratch folder that git ignores), **not** to `destination/`.

```bash
.venv/bin/python - <<'EOF'
from pathlib import Path
from openai import OpenAI
from folder_watcher.config import load_config

cfg = load_config()
s = cfg.llm.sampling_translate
client = OpenAI(base_url=cfg.llm.base_url + "/v1", api_key="not-needed")
text = Path("tests/samples/laporan_mingguan.md").read_text()
reply = client.chat.completions.create(
    model=cfg.llm.alias,
    messages=[
        {"role": "system", "content": "Translate the user's text to English. Output only the translation. "
         "Keep the Markdown structure exactly. Do not translate code blocks, inline code or URLs. "
         "Keep proper nouns as they are."},
        {"role": "user", "content": text},
    ],
    temperature=s.temperature, top_p=s.top_p, presence_penalty=s.presence_penalty,
    extra_body={"top_k": s.top_k, "min_p": s.min_p},
)
out = Path("state/laporan_mingguan.en.md")
out.write_text(reply.choices[0].message.content + "\n")
print(f"Wrote {out}")
EOF
```

What you should see: `Wrote state/laporan_mingguan.en.md` after a few seconds.

**B9. Read the translation.**

```bash
cat state/laporan_mingguan.en.md
```

What you should check: it is in English, with the same headings, lists and code block as the original. Example (beginning only):

```text
# Weekly Project Report

To all team members,

This week we have completed the first part of the folder monitoring system. ...
```

**B10. Check the Markdown structure survived.** This compares the headings, list markers and code fences of the original and the translation, line by line.

```bash
diff <(grep -oE '^(#+|-|[0-9]+\.|```)' tests/samples/laporan_mingguan.md) <(grep -oE '^(#+|-|[0-9]+\.|```)' state/laporan_mingguan.en.md) && echo "Structure matches"
```

What you should see: `Structure matches`. Anything else (lines starting with `<` or `>`) means the structure changed.

**B11. Check the command inside the code block is unchanged.**

```bash
grep -F 'ls -la source/' state/laporan_mingguan.en.md
```

What you should see: `ls -la source/`. No output means the command was changed.

**B12. Check the link is unchanged.**

```bash
grep -oF 'https://example.com/projek' state/laporan_mingguan.en.md
```

What you should see: `https://example.com/projek`.

**B13. Check there is no "thinking" text in the output.**

```bash
grep -c '<think>' state/laporan_mingguan.en.md
```

What you should see: `0`.

**B14. Known limitation: the comment inside the code block.**

```bash
grep -F '# Papar senarai fail dalam folder sumber' state/laporan_mingguan.en.md || echo "Comment was translated (known limitation until Milestone 4)"
```

What you will most likely see today: `Comment was translated (known limitation until Milestone 4)`. The model tends to translate the comment inside the code block even when told not to. A prompt is not a guarantee, so Milestone 4 will protect code blocks **in code**: they will be taken out before translation and put back afterwards. After Milestone 4, this check should print the original Malay comment.

**B15. Check the model can tell English from non-English.** The agent's first decision (Milestone 4) is whether a file is already English, in which case it is skipped, not translated. This step asks the model that question about both sample files: `tests/samples/english_note.txt` (a short English note) and the Malay report. It uses the agent settings from `config/config.toml`.

```bash
.venv/bin/python - <<'EOF'
from pathlib import Path
from openai import OpenAI
from folder_watcher.config import load_config

cfg = load_config()
s = cfg.llm.sampling_agent
client = OpenAI(base_url=cfg.llm.base_url + "/v1", api_key="not-needed")
for name in ["tests/samples/english_note.txt", "tests/samples/laporan_mingguan.md"]:
    reply = client.chat.completions.create(
        model=cfg.llm.alias,
        messages=[
            {"role": "system", "content": "Is most of the user's text written in English? "
             "Answer with exactly one word: yes or no."},
            {"role": "user", "content": Path(name).read_text()},
        ],
        temperature=s.temperature, top_p=s.top_p, presence_penalty=s.presence_penalty,
        extra_body={"top_k": s.top_k, "min_p": s.min_p}, max_tokens=5,
    )
    print(f"{name}: English? {reply.choices[0].message.content.strip()}")
EOF
```

What you should check: **yes** for `english_note.txt` and **no** for `laporan_mingguan.md`. (It gave the same answers in 5 out of 5 runs on the reference machine.) Example:

```text
tests/samples/english_note.txt: English? yes
tests/samples/laporan_mingguan.md: English? no
```

This only shows the model *can* make the decision. Actually skipping the English file, with the reason in the log, arrives with the agent in Milestone 4.

**B16. Terminal 1: stop the model server.** Press **Ctrl+C** in Terminal 1.

What you should see: a line ending in `cleaning up before exit...`, then your normal prompt.

**B17. Terminal 2: check the server is gone.**

```bash
curl -s http://127.0.0.1:8080/health
```

What you should see: nothing at all. `curl` cannot connect any more.

**B18. (GPU only) Check the GPU memory was freed.**

```bash
nvidia-smi --query-gpu=memory.used,memory.total --format=csv
```

What you should see: about the same "used" number as in A6. Example: `786 MiB, 8192 MiB`.

### Optional: see the GPU offload in the server log

The normal server log does not list where each layer went. To see it, start the server in Terminal 1 with more detailed logging, filtered down to the two lines that matter:

```bash
LLAMA_ARG_LOG_VERBOSITY=4 .venv/bin/python -m folder_watcher llm-server 2>&1 | grep --line-buffered -E "offloaded|listening"
```

What you should see (on a GPU machine):

```text
0.01.344.840 I load_tensors: offloaded 33/33 layers to GPU
0.02.285.892 I srv  llama_server: listening on http://127.0.0.1:8080
```

`33/33` means the whole model is on the GPU. Press **Ctrl+C** to stop it.

### Not testable yet

| Feature | Available after |
|---|---|
| Agent deciding between translate and skip, with tools | Milestone 4 |
| Watching `source/`, writing to `destination/`, live on/off toggle | Milestone 5 |
| `scripts/start.sh`, `stop.sh`, `status.sh`, `logs.sh` and the systemd services | Milestone 6 |
| `python -m folder_watcher check` (currently prints "not built yet") | Milestone 6 |
| CPU-only mode (`gpu_layers = "0"`) and the full acceptance checklist | Milestone 7 |

---

## Using it (planned)

> **Not available yet.** The watcher arrives in Milestone 5 and the `scripts/start.sh`, `logs.sh`, `status.sh` and `stop.sh` services in Milestone 6. These commands are the **plan** from `SPEC.md` and will be confirmed and corrected once they are built. What you can test today is in [Testing it](#testing-it).

Start the services (this also loads the model, which can take a minute):

```bash
scripts/start.sh
```

Watch what the agent is doing:

```bash
scripts/logs.sh
```

Drop a file in `source/` (from another terminal tab):

```bash
cp my_french_note.txt source/
```

After a short wait, `destination/my_french_note.en.txt` appears. The log shows every step the agent took, including *why* it skipped or translated.

Switch watching off and on without restarting (`config/config.toml`, section `[watch]`):

```toml
[watch]
enabled = false      # change to true to resume
extensions = [".txt", ".md"]
```

> Files that arrive while watching is **off** are ignored for good. They are not processed when you switch it back on.

Check status, and stop when you are finished (this also frees the GPU memory):

```bash
scripts/status.sh
scripts/stop.sh
```

The services never start on their own. After you close WSL or reboot, run `scripts/start.sh` again.

---

## Using a smaller model

If the 9B model does not fit your GPU memory, use the 4B version from the same family:

| Model | File | Size |
|---|---|---|
| Qwen3.5-9B (default) | `Qwen3.5-9B-Q4_K_M.gguf` | about 5.7 GB |
| **Qwen3.5-4B** | `Qwen3.5-4B-Q4_K_M.gguf` | about 2.7 GB |

Repository: [unsloth/Qwen3.5-4B-GGUF](https://hf.co/unsloth/Qwen3.5-4B-GGUF). A rough rule: the model file plus another one to two gigabytes for working memory should fit in your free GPU memory (check with `nvidia-smi`). If it does not, the server can place some layers on the CPU. That works but is slower.

To switch, ask Claude Code:

```text
Switch the project to Qwen3.5-4B Q4_K_M from unsloth/Qwen3.5-4B-GGUF.
Download it into models/, update model_path and alias in config/config.toml,
restart the services, and confirm it works by translating a sample file.
```

The config change it will make is only this (in `config/config.toml`):

```toml
[llm]
model_path = "models/Qwen3.5-4B-Q4_K_M.gguf"
alias = "qwen3.5-4b"
```

Smaller models translate less accurately and follow tool instructions less reliably, so expect to test the results.

**No GPU at all?** Everything is designed to run on the CPU too: `scripts/build_llama.sh` detects a missing GPU and builds for CPU, and `gpu_layers = "0"` in `[llm]` is meant to force CPU use even when a GPU is present. *CPU mode has not been tested yet; that is part of the final acceptance checks.* A smaller model is the better choice on CPU.

---

## Extending: adding a new file type (design preview)

Each file type is a **reader tool** plus a config entry. For example, to add Word files later:

1. Add a module `tools/readers_docx.py` that provides a `read_docx` tool for `.docx`.
2. Add the library it needs as an optional dependency.
3. Add `".docx"` to `extensions` in `[watch]`.

The agent loop does not change. The agent is only shown the tools that fit the file in front of it.

---

## Project files

| File | Purpose |
|---|---|
| [`SPEC.md`](SPEC.md) | The complete design and acceptance checklist |
| [`CLAUDE.md`](CLAUDE.md) | Working rules for Claude Code |
| `config/config.toml` | The one config file |
| `source/` | Watched folder: drop files here |
| `destination/` | Translated files appear here |

## Troubleshooting (setup)

| Symptom | Fix |
|---|---|
| `claude: command not found` | Run `export PATH="$HOME/.local/bin:$PATH"` in the current terminal, then check again. |
| `ps -p 1 -o comm=` does not print `systemd` after the restart | Check `/etc/wsl.conf` has the two `[boot]` lines, then run `wsl.exe --shutdown` in PowerShell again. Make sure WSL is up to date (`wsl --version`). |
| `nvidia-smi` not found or shows no GPU in Ubuntu | Update the NVIDIA driver on **Windows** and update WSL. Do not install a driver inside Ubuntu. |
| `gh auth status` says you are not logged in | Run `gh auth login` again. |
| `git pull` or `git push` asks for a password | Run `gh auth setup-git`, then try again. |
| `E: Unable to locate package` | Run `sudo apt update` first. |

*This README is updated as each milestone is finished. It currently reflects Milestones 1 to 3.*
