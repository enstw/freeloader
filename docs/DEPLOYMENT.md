# Deployment: `moon` (marble-server)

The author's canonical FreelOAder deployment. This file tracks the live
installation so it can be rebuilt or audited from this repo alone, even
though the actual launchers live in the sibling
[`marble-server`](https://github.com/enstw/marble-server) repo.

For the FreelOAder-side run / install steps (uvicorn invocation, host
setup, `UV_CACHE_DIR=/tmp` workaround), see the "Deploy on a dedicated
host" section in [`../README.md`](../README.md). This file is the
*operational* layer on top of that.

## Host

`moon` — Ubuntu 26.04 ARM64 chroot on a Poco F5 5G phone running
LineageOS 23.2 + KernelSU-Next. Provisioned by `marble-server` (see its
`docs/INSTALLATION.md`).

| Reach via | Endpoint |
|---|---|
| LAN | `ssh user@moon` (OpenSSH on port 2222) |
| WAN | Tailscale SSH to the `moon` machine name |

## On-host layout

| Concern | Path |
|---|---|
| FreelOAder repo | `/home/user/freeloader` (deployment clone — `git pull` for updates) |
| FreelOAder venv | `/home/user/freeloader/.venv` |
| FreelOAder bind | `127.0.0.1:8000` (loopback only — no public exposure) |
| Hermes binary | `/home/user/.local/bin/hermes` |
| Hermes venv | `/home/user/.hermes/hermes-agent/venv` |
| Hermes data | `/home/user/.hermes/` |
| Agent logs | `/home/user/.local/state/moon-agents/{freeloader,hermes}.log` |
| Boot launcher | `/etc/host-hooks/agents_start.sh` (deployed by marble-server) |
| Boot enable flag | `/etc/host-hooks/agents.enabled` (touch to opt in) |

## Boot path

There is no PID 1 / systemd-user inside this chroot, by design. Startup
is wired through Android's KernelSU service hooks:

```
KSU-Next module `moon-ssh`           (Android late_start service)
    └─ scripts/ksu-moon-ssh/service.sh
        └─ /etc/host-hooks/agents_start.sh        ← gated by /etc/host-hooks/agents.enabled
            ├─ tmux-service freeloader -- … uvicorn freeloader.frontend.app:create_app …
            └─ tmux-service hermes      -- hermes gateway run --replace
```

`tmux-service` is a small helper that runs a command inside a named
detached `tmux` session with a `while true; do <cmd>; sleep 2; done`
crash loop and a `tee -a <log>` log sink — the chroot's stand-in for
`Restart=always`. Source of truth in `marble-server`:

- `~/marble-server/scripts/agents_start.sh` — boot launcher (which agents,
  in what order, with what args).
- `~/marble-server/scripts/tmux-service.sh` — the supervisor helper
  (installed at `/usr/local/bin/tmux-service` by `agents_setup.sh`).

Do **not** mirror those scripts into this repo. They evolve in
`marble-server` alongside the chroot's other init.

## Operate

```bash
ssh user@moon

# Watch live (Ctrl-b d to detach without killing).
tmux attach -t freeloader
tmux attach -t hermes

# Tail logs without attaching.
tail -f ~/.local/state/moon-agents/freeloader.log

# Health check.
curl -sS http://127.0.0.1:8000/v1/models | jq .

# Stop a service. The boot launcher will not respawn it until next boot;
# tmux-service has no "enable on next start" notion separate from the
# agents.enabled flag.
tmux kill-session -t freeloader
```

## Update FreelOAder on moon

```bash
ssh user@moon
cd ~/freeloader
git pull
uv sync                               # or the UV_CACHE_DIR=/tmp/uv-cache
                                      # workaround if f2fs+SELinux setxattr
                                      # trips during the editable build
tmux kill-session -t freeloader       # boot launcher will not respawn —
                                      # restart it now from the same shell:

tmux-service freeloader -- sh -c \
  'cd /home/user/freeloader && exec .venv/bin/uvicorn \
   freeloader.frontend.app:create_app --factory \
   --host 127.0.0.1 --port 8000'
```

A reboot also works and is sometimes simpler — `agents_start.sh` runs at
`late_start service` while `agents.enabled` is present.

## Disable boot startup without uninstalling

```bash
ssh user@moon
sudo rm /etc/host-hooks/agents.enabled    # touch it again to re-enable
tmux kill-session -t freeloader
tmux kill-session -t hermes
```

The `marble-server` install of `agents_start.sh` is gated entirely on
that flag — removing it leaves both binaries and configs in place but
silences the boot launcher.
