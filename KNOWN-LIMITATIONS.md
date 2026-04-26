# Known limitations

State that the spawned CLI subprocesses inherit from the host, even after
adapter-level minimization flags are applied. Documented here so operators
running FreelOAder against real subscriptions know exactly what's leaking
into each turn (and can decide whether to apply the deploy-time recipe
that addresses the biggest piece of it).

## Memory inheritance

By default, each CLI loads project-wide memory from a global file:

- claude: `~/.claude/CLAUDE.md`
- codex: `~/.codex/AGENTS.md`
- gemini: `~/.gemini/GEMINI.md`

There is no command-line flag on any of the three CLIs that disables
this load while keeping OAuth working (`claude --bare` would, but it
strictly disables OAuth and forces ANTHROPIC_API_KEY-only auth, which
defeats the purpose of FreelOAder). Per-conversation env-var redirection
(`CLAUDE_CONFIG_DIR`, `CODEX_HOME`, `GEMINI_CLI_HOME`) strips OAuth as
well — verified empirically on 2026-04-25 / 2026-04-26.

**Fix on a dedicated host:** run `./scripts/setup-host.sh --yes`. This
symlinks the three memory files to `/dev/null` so the CLIs read 0 bytes,
and empties the separate `~/.codex/memories` and `~/.gemini/memory`
directories that are also auto-loaded. **Do not run on a workstation**
where you also use these CLIs interactively — the symlinks are global
to the running user.

## Residuals after adapter flags + setup script

Even with everything above applied, each CLI still loads:

- **Built-in tools.** All three CLIs ship a fixed set of native tools
  (file read/edit, shell, search, etc.) that are part of the binary and
  cannot be disabled via flags or config. These contribute to the cold-
  cache system prompt regardless of what we do.
- **Plugins under `~/.claude/plugins/`.** claude has no `--no-plugins`
  flag; `--strict-mcp-config` only affects MCP servers, not plugins.
  Removing the directory works but is more invasive than this project
  wants to recommend by default.
- **Gemini skills.** `gemini skills disable <name>` is per-skill via a
  subcommand. There is no global "no skills" flag. If you have skills
  installed and don't want them loaded, you must disable each by name.
- **Gemini extensions.** The adapter passes `-e _freeloader_none` (a
  sentinel name that matches no real extension). yargs may emit a
  warning to stderr; this is acceptable since stderr is discarded.

## What full suppression would need

A per-conversation `CLAUDE_CONFIG_DIR` / `CODEX_HOME` / `GEMINI_CLI_HOME`
seeded with **only** the OAuth-relevant fields would let the CLIs auth
against the user's subscription while loading nothing else (no MCP, no
skills, no plugins, no memory, no settings). This was deferred because
on macOS the OAuth secret is partially keychain-coupled and enumerating
the relevant keychain attributes wasn't authorized in the session that
landed the current minimization. Revisit when there's a documented
keychain attribute layout, or when a non-macOS deployment forces a Linux-
side investigation that has no keychain to work around.

## Cold-cache tax

Each CLI invocation eats some number of input tokens just to load its
own system prompt before the user's prompt is seen. The flag-based
minimization plus the setup script reduce this substantially, but it
will never reach zero: built-in tools and the agent-prompt scaffold are
mandatory.

This was sized at 6k–14k input tokens per cold turn during the 2026-04-05
spike (pre-minimization). Post-minimization measurements should land in
`JOURNAL.jsonl` once they're collected from real operation.
