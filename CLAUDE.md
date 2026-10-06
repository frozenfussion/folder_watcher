# CLAUDE.md

Project: **Folder Watcher**, an on-prem agent that watches `source/`, translates new non-English documents to English with a local open-weight model, and writes the result to `destination/`.

The full design is in **`SPEC.md`**. Read it completely before writing any code. This file only says how to work. If `SPEC.md` and this file ever disagree, stop and ask Aziz.

## Who you are working with
- The user is **Aziz**, an instructor. He is building this live in front of students, and many of them are beginners.
- Explain what you are about to do in one plain sentence before you do anything non-obvious (installing, building, editing system files).
- Keep output readable. Prefer short, clear steps over long walls of text.
- Do not assume anything is installed. Check, then act.
- Be honest. If something failed, say so with the real error. If you skipped something, say that. Never claim a test passed that you did not run.
- If you make a mistake, say so plainly and fix it.

## Environment
- WSL2 (Ubuntu) on Windows. Project root: `~/projects/folder_watcher`. Keep everything on the Linux filesystem, never under `/mnt/c`.
- GPU: an NVIDIA card may or may not be present. **Detect it, never assume it.** The code and scripts must work on CPU-only machines.
- You run inside the same WSL terminal Aziz is using. You can run commands, but you **cannot type a sudo password**.

## The sudo rule (important)
1. Do everything that does not need root yourself.
2. If something needs root (`apt install`, editing `/etc/...`, installing the CUDA toolkit), **do not try it and do not retry in a loop**. Write a small, commented, idempotent script in `scripts/system/`, then tell Aziz the exact command to run, for example:
   `sudo bash scripts/system/01_base_packages.sh`
   Then wait for him to say it is done and verify the result yourself.
3. Never use `sudo pip`. Python packages go into `.venv` only.
4. Never install an NVIDIA Linux driver inside WSL. See `SPEC.md` section 10.2.

## Installation hurdles: handle them, do not hand them back
When you hit a problem (missing tool, PATH, permissions, a failed download, a build error), solve it yourself where you can:
- **PATH**: never tell Aziz to close and reopen the terminal. Fix it for the current shell with an `export PATH=...` line (or call the tool by absolute path), and also persist it in `~/.bashrc`. Scripts should use absolute paths.
- **Permissions on your own files**: fix with `chmod` or by moving files into the project. Never loosen permissions on system paths.
- **Build errors in llama.cpp**: read the error, check the build docs, fix, rebuild. If the model fails to load, first try a newer llama.cpp. Qwen3.5 is a recent architecture.
- **Port in use**: pick another port in `config/config.toml` and tell Aziz.
- **Model download**: retry, then report. If it needs a Hugging Face login, ask Aziz to run the login himself. Never ask him to paste a token into the chat, and never write a token into a file in the repo.
- Only ask Aziz a question when you are truly blocked, and make it a specific question.

## Do not guess
- Flags, package names, and URLs change. Before relying on a llama.cpp flag, run `llama-server --help` from the build. Before the CUDA step, read NVIDIA's current WSL-Ubuntu download page. Items marked `VERIFY` in `SPEC.md` are exactly the ones to check.
- If you cannot verify something, say so in your message and in a code comment. Do not invent.

## How to build
Work in this order, and check in with Aziz after each milestone with a short summary:
1. **Scaffold**: `pyproject.toml`, package layout, config loader, `.venv` via `scripts/setup_python.sh`.
2. **System prerequisites**: write `scripts/system/01_base_packages.sh` (and `02_cuda_toolkit_wsl.sh` if a GPU is present). Hand them to Aziz to run.
3. **Model server**: `scripts/build_llama.sh`, `scripts/download_model.sh`, `launch_llm.py`. Prove the server answers `GET /health` and a simple chat request, and that GPU offload happens when a GPU exists.
4. **Tools + agent loop** against the running server. Test the agent on a hand-made task before wiring the watcher.
5. **Watcher**: new-files-only, stability check, live toggle.
6. **Services**: `scripts/install_services.sh`, `start.sh`, `stop.sh`, `status.sh`, `logs.sh`. Remember: units are installed but **never enabled**.
7. **Tests** and the acceptance checklist in `SPEC.md` section 15.

## Code conventions
- Python 3.11+ if available (see `SPEC.md` section 2 for the `tomli` fallback). Type hints on public functions.
- Small modules, matching the layout in `SPEC.md` section 5. The agent loop should be short enough to read in one sitting, because students will read it.
- Standard library first. Only the dependencies listed in `SPEC.md` section 12a. No agent frameworks.
- Comments explain *why*, not *what*. Keep them short.
- Guardrails (path safety, extension allow-list, step limit) are enforced in code, never only in a prompt.
- Shell scripts: `#!/usr/bin/env bash`, `set -euo pipefail`, idempotent, print what they are doing, and use absolute paths derived from the script's location.
- Never write secrets, tokens, or personal paths into the repo.

## Hard rules for this project
- The services must **not** start automatically. Never run `systemctl --user enable` and never add an `[Install]` section to the units.
- Only new files trigger work. Files already in `source/` at service start are ignored.
- `watch.enabled` and `watch.extensions` are live-reloaded. `[llm]` settings are not.
- The model server binds to `127.0.0.1` only.
- The agent must be able to choose: an English document is skipped, not translated, and the log must show the reason.
- No cloud API calls at runtime.
- Do not read `source/` file contents into logs above DEBUG level.

## Git
- Commit in small, meaningful steps with clear messages.
- Do not push, open pull requests, or change remotes unless Aziz asks.
- Never commit `models/`, `vendor/`, `.venv/`, `state/`, or log files (they are in `.gitignore`).

## Documentation
- When the build is finished, Aziz will ask you to update `README.md` to match what was actually built. Until then the README's usage section describes the plan. Do not rewrite the README unprompted.
- Keep `SPEC.md` accurate. If you deviate from it for a good reason, record the deviation and the reason in a short "Deviations" section at the bottom of `SPEC.md`.
- If you learn that a `VERIFY` item was wrong, correct it in `SPEC.md`.

## Commands you will use often
Once built (names are defined in `SPEC.md`):
```bash
scripts/status.sh                         # what is running, GPU/CPU backend, config state
scripts/start.sh                          # start both services (manual, never at boot)
scripts/stop.sh                           # stop both
scripts/logs.sh                           # follow logs
python -m folder_watcher check            # preflight checks
python -m folder_watcher config set watch.enabled false   # live toggle
pytest                                    # unit tests (no model needed)
```
