# hermes-2 VM implementation plan

Status: implementation plan only. This document authorizes no Proxmox, network, account, or VM changes.

## Scope and fixed design

Create VM `10182` (`hermes-2`) on `pve1`: q35/OVMF, 1 socket × 8 cores, CPU `host`, 10 GiB RAM, VirtIO SCSI Single, a 128 GiB thin-provisioned disk on `datastore2`, QEMU Guest Agent, and one untagged VirtIO NIC on `vmbr0` with its per-NIC Proxmox firewall flag off. Enable autostart. Install the latest official Ubuntu 26.04 LTS Desktop point release available on execution day (26.04.1 is current when this plan was written), GNOME, and the specified tools. Do not add Docker, local model endpoints, Claude Code, SSH keys, GitHub authentication, Pi provider/model configuration, or a backup job.

Authoritative release checks: Canonical lists [Ubuntu 26.04 LTS support through 2031](https://ubuntu.com/about/release-cycle), publishes [26.04 release notes and point releases](https://documentation.ubuntu.com/release-notes/26.04/), and supplies ISO checksums/signatures in the [official release directory](https://releases.ubuntu.com/26.04/). Recheck these on execution day. Validate all `qm` options against the installed `qm(1)`/`qm.conf(5)` on `pve1`, not an assumed Proxmox version.

## Authentication and secret handling

Interactive authentication points are:

1. Proxmox GUI login or SSH to `pve1` as `<PROXMOX_ADMIN>` (and an interactive privilege-elevation prompt if required).
2. Ubuntu installer prompts for `<GLENN_PASSWORD>`; type it only into the installer.
3. `sudo adduser ned` prompts for `<NED_PASSWORD>`; type it at the hidden prompt. Subsequent `sudo` may request `<GLENN_PASSWORD>` until `ned` is validated.
4. NoMachine package download may require acceptance or portal authentication; use `<NOMACHINE_PORTAL_CREDENTIAL>` only in the official interactive flow.
5. MeshCentral enrollment uses `<MESH_SERVER_URL>` and a server-generated `<MESH_ENROLLMENT_TOKEN_OR_COMMAND>`; paste interactively and do not save it in shell history, scripts, tickets, or this file.
6. First logins to Hermes, Codex, Pi, or HerdR may open device/browser authentication. Use `<SERVICE_ACCOUNT>` and the official interactive flow; never pass API keys/tokens on a command line. GitHub CLI authentication is explicitly deferred.

Do not enable shell tracing. Do not put passwords/tokens in cloud-init, environment files, command arguments, clipboard logs, screenshots, terminal transcripts, or Proxmox task descriptions. Clear any sensitive clipboard after use. The example commands below contain identifiers only, never credentials.

## Phase 1 — PLAN

### Preconditions and decisions to record

- Record executor, maintenance window, installed Proxmox VE version, change ticket, and console/out-of-band access.
- Confirm `datastore2` is VM-image capable, active on `pve1`, thin provisioning is genuinely supported by its backend, has at least 128 GiB logical capacity plus EFI/snapshot headroom, and supports snapshots. A `dir` backend with qcow2 can be sparse but is not equivalent to LVM-thin; resolve this before creation.
- Confirm where the ISO and small EFI variables disk will live. The examples use `<ISO_STORAGE>` for the ISO and `datastore2` for EFI. Confirm Secure Boot policy; the example uses OVMF 4 MiB variables with Microsoft pre-enrolled keys. No TPM is required by the approved specification.
- Confirm 10 GiB means 10,240 MiB, no ballooning requirement, and the host has CPU/RAM capacity. CPU type `host` reduces live-migration portability.
- Confirm IPAM/DHCP exclusion or reservation for `10.1.10.182`, subnet `/16`, gateway `10.1.1.1`, DNS order `10.1.0.102`, `10.1.1.102`, and untagged `vmbr0`. A `/16` makes both gateway and address on-link; verify that is intentional.
- Confirm the exact upstream identity/repository/product meant by “Hermes,” “Pi,” and “HerdR,” plus supported Ubuntu/Node/Python versions and required integration. These names are ambiguous; do not guess.
- Confirm `<MESH_SERVER_URL>`, the target MeshCentral device group, enrollment method, outbound ports, and whether “MeshCentral-compatible” means installing its native agent. Confirm NoMachine edition/licensing and inbound firewall policy.
- Confirm the policy for `glenn`: the installer necessarily creates it as the initial sudo-capable user. After `ned` passwordless sudo is proven, either retain `glenn` in `sudo` or remove it per owner decision.
- Confirm VM `10182` is excluded from every cluster-wide “all guests” backup job. “No backup job” cannot be achieved merely by not creating a new job if an existing catch-all job includes it; changing an existing job is outside this runbook and needs separate approval.

### Read-only preflight — Proxmox host

Run on `pve1`; save sanitized output in the change record. These commands are read-only:

```bash
pveversion -v
pvecm nodes
qm status 10182
qm list
pvesm status
pvesm list datastore2
ip -details link show vmbr0
pvesh get /cluster/backup --output-format json
qm help create
qm help set
```

Pass criteria:

- `pve1` is the intended node and healthy. `qm status 10182` reports that the configuration does not exist, and `qm list` contains neither VMID `10182` nor name `hermes-2`. Also check cluster GUI search because names are not necessarily unique-enforced.
- `datastore2` is active, accepts `images`, has adequate physical/logical/snapshot headroom, and its backend meets the thin-provisioning intent.
- `vmbr0` exists and is up; its VLAN-awareness/uplink configuration is compatible with an untagged guest. Do not alter the bridge or host network.
- Existing backup jobs do not select VMID 10182 via explicit or all-guests selection; if they will, stop for separate approval.
- Installed help supports every proposed option below. Check the GUI/API schema as a second source if the Proxmox version differs from the reviewed syntax.

### ISO authenticity and release preflight

From an admin workstation or a designated ISO staging area—not by piping downloads into a shell—confirm the latest supported 26.04 Desktop point release and download the AMD64 ISO, `SHA256SUMS`, and `SHA256SUMS.gpg` from the same official Canonical directory. Verify the signature using a trusted Ubuntu image-signing key obtained through Canonical's published verification procedure, then verify the ISO with `sha256sum -c`. Record filename, digest, signature result, source URL, and time. Upload/import it into `<ISO_STORAGE>` and verify its post-upload digest. Do not proceed on a checksum-only match if the checksum file's signature has not been authenticated.

### IP conflict preflight

Coordinate with IPAM/DHCP/network administration first. Check DHCP leases/reservations, DNS forward/reverse records, ARP/neighbor and switch tables, and monitoring. From the correct L2 segment, probe `10.1.10.182` while noting that ping/ARP silence does **not** prove the address is free. Reserve/exclude the address before use. Do not run duplicate-address testing from `pve1` if that would require changing host interfaces.

## Phase 2 — REVIEW (go/no-go before changes)

Two-person review the recorded values and the exact command generated for the installed Proxmox version. Confirm there are no unresolved placeholders except secrets that will be typed interactively. Confirm ISO signature/digest, free VMID/name, storage/bridge/IP, snapshot capability, backup exclusion, and console access. Capture the current `qm list`, storage status, backup-job selection, and network evidence. A failed or ambiguous check is a no-go.

Safe construction principles:

- Every mutating Proxmox command must explicitly target only VMID `10182`; never loop over guests, use globs, edit `/etc/pve/qemu-server` directly, or change datacenter/storage/bridge/host boot configuration.
- Create the empty VM first, inspect `qm config 10182`, then add one device at a time. Do not reuse an existing VMID, disk volume, MAC, or EFI volume.
- Quote the boot-order argument because `;` is a shell metacharacter. Keep NIC VLAN omitted (untagged) and set `firewall=0` on `net0`. Verify VM-level firewall options in the GUI are disabled as well; do not disable datacenter/node firewalling.
- Prefer GUI/API fields if the installed CLI schema differs. Never “fix” a rejected option by guessing.

## Phase 3 — EXECUTE

### A. Proxmox-host commands (run only after approval)

First substitute only validated non-secret placeholders. The following is a reviewed command pattern, not a script:

```bash
qm create 10182 --name hermes-2 --ostype l26 --machine q35 --bios ovmf \
  --cpu host --sockets 1 --cores 8 --memory 10240 --scsihw virtio-scsi-single \
  --agent enabled=1 --onboot 1

qm set 10182 --efidisk0 datastore2:1,efitype=4m,pre-enrolled-keys=1
qm set 10182 --scsi0 datastore2:128,discard=on,iothread=1
qm set 10182 --net0 virtio,bridge=vmbr0,firewall=0
qm set 10182 --ide2 '<ISO_STORAGE>:iso/<VERIFIED_UBUNTU_26_04_DESKTOP_ISO>,media=cdrom'
qm set 10182 --boot 'order=ide2;scsi0'
qm config 10182
```

Before running, use `qm help create`, `qm help set`, and storage/API schema to validate allocation syntax. `datastore2:128` asks Proxmox to allocate a new 128 GiB volume; thin behavior comes from the confirmed storage backend. Do not add `format=` unless valid and intentional for that backend. The EFI volume is separate from the 128 GiB OS disk. Let Proxmox generate the NIC MAC unless IPAM requires a pre-approved one. Do not add a TPM, VLAN tag, ballooning, PCI passthrough, or cloud-init drive.

### B. Proxmox GUI/API actions

- In VM 10182 Hardware/Options, independently verify q35, OVMF, 8 cores/1 socket, CPU host, 10,240 MiB, VirtIO SCSI Single, 128 GiB on `datastore2`, EFI disk, guest agent enabled, NIC `vmbr0`/VirtIO/no VLAN/firewall unchecked, and Start at boot enabled.
- Confirm VM Firewall is disabled without altering datacenter/node firewall settings.
- Start VM 10182 and open its console. Do not modify host boot order; the `qm set --boot` setting is guest-only.

### C. Ubuntu installer actions

- Choose “Install Ubuntu,” normal GNOME Desktop installation, use the entire **128 GiB VM disk only**, and install third-party media support only if policy permits. Confirm the target disk size before committing.
- Set hostname/computer name `hermes-2`, timezone `Australia/Brisbane`, and create `glenn`; type `<GLENN_PASSWORD>` interactively. Do not enable automatic login, disk encryption, domain join, Ubuntu Pro, or third-party remote services unless separately approved.
- Reboot. Detach the ISO in Proxmox, set guest boot order to disk only, and verify boot. On the host:

```bash
qm set 10182 --ide2 none,media=cdrom
qm set 10182 --boot 'order=scsi0'
qm config 10182
```

### D. Commands inside the VM

Run interactively as `glenn` initially. Review package changes before accepting; never use unattended `curl | sh` patterns.

1. Establish identity, patches, and guest integration:

```bash
hostnamectl
timedatectl
sudo apt update
sudo apt full-upgrade
sudo apt install openssh-server qemu-guest-agent git build-essential jq ripgrep tmux unzip ffmpeg curl ca-certificates gnupg
sudo systemctl enable --now ssh qemu-guest-agent
sudo hostnamectl set-hostname hermes-2
sudo timedatectl set-timezone Australia/Brisbane
```

Reboot if required. Treat `ffmpeg` as installed only if needed/approved after reviewing package impact.

2. Configure static networking through the Ubuntu Desktop NetworkManager connection, preferably using GNOME Settings to avoid mistyping the active connection: IPv4 Manual, address `10.1.10.182`, prefix `16`, gateway `10.1.1.1`, DNS `10.1.0.102, 10.1.1.102`, automatic DNS off. If CLI is preferred, first discover `<CONNECTION_NAME>` with `nmcli connection show`; then use documented `nmcli connection modify` syntax for the installed release. Keep console access open, activate the connection, and verify before ending the current session. Do not guess an interface name or create competing Netplan/NetworkManager profiles.

3. Create and secure accounts:

```bash
sudo adduser ned
sudo usermod -aG sudo ned
sudo install -o root -g root -m 0440 /dev/null /etc/sudoers.d/ned
sudo visudo -f /etc/sudoers.d/ned
```

In `visudo`, enter exactly `ned ALL=(ALL:ALL) NOPASSWD: ALL`, validate syntax, then open a new `ned` login and prove `sudo -n true`. Only after that, apply the confirmed decision on whether `glenn` remains in `sudo`. Never edit `/etc/sudoers` with an ordinary editor.

4. Configure SSH using a root-owned drop-in such as `/etc/ssh/sshd_config.d/60-hermes.conf` with `PasswordAuthentication yes` and `PermitRootLogin no`. Validate with `sudo sshd -t`, reload (not restart) SSH, and test a second session as both `glenn` and `ned` before closing the console. Confirm no keys exist initially. Password SSH increases brute-force risk; network ACLs and strong unique passwords are required.

5. As each human/automation user, create its own workspace—never a shared root-owned tree:

```bash
mkdir -p "$HOME/projects"
chmod 700 "$HOME/projects"
```

Agent projects and configuration must stay under the invoking user's home, principally `~/projects`; no worktrees in `/root`, `/opt`, `/usr/local`, or another user's home. Use `sudo` only for OS packages/services, never for npm, uv, browsers, agent CLIs, repositories, or project commands.

6. Browsers and Playwright:

- Confirm GNOME session and the installed default Firefox policy. Install Chromium using Ubuntu's supported 26.04 mechanism (likely the Ubuntu snap) and Chrome from Google's official signed repository/package after reviewing its current official instructions and key fingerprint. Do not use unofficial PPAs.
- Install Playwright in a non-root test project under `~/projects`; pin it in that project's manifest. Use the installed Playwright release's official Ubuntu dependency procedure at execution time. System dependency installation may require one reviewed `sudo` action; browser binaries and npm cache must remain user-owned. Do not run `sudo npx`, `sudo npm`, or put a project-global Playwright in `/usr/local`.

7. Language/toolchain strategy:

- Install `uv` per Astral's official, checksum/verifiable instructions current on execution day, as `ned` (and separately for `glenn` only if needed). Ensure its user bin directory is deliberately added once to that user's login PATH and is visible in a fresh login. Let `uv` manage project Python versions/environments under each user's home; do not replace `/usr/bin/python3` or pip-install into system Python.
- Use one Node strategy: default to the latest supported **LTS** from an official Node distribution method verified on execution day (Node 24 LTS at plan time; [check current status](https://nodejs.org/en/about/previous-releases)). Install/manage it as `ned`, not root, and ensure one `node`/`npm` pair wins in a fresh login. Do not mix Ubuntu `nodejs`/`npm`, NodeSource, `nvm`, `fnm`, or other managers. Do not globally install tools with sudo.
- Install GitHub CLI from its official signed repository/package instructions, verify `gh --version`, but do not run `gh auth login`.

8. Remote access and agents—execution-time documentation gate:

- **NoMachine:** do not use an install command from this plan. Verify the current official NoMachine Linux/Ubuntu 26.04 package, checksum/signature, licensing, and service/firewall instructions; download without embedding credentials and install only the reviewed package. Test GNOME session behavior after reboot.
- **MeshCentral:** do not use a generic internet installer. In the approved MeshCentral server UI, generate the native Linux agent/enrollment for the confirmed device group, inspect the exact official command/package, treat its token as a secret, and enroll interactively. Verify server identity/TLS before enrollment and service ownership afterward.
- **Hermes, OpenAI Codex CLI, Pi, HerdR:** this plan intentionally supplies **no installation commands**. At execution time, identify each approved official upstream, verify its current Ubuntu 26.04 and Node/Python prerequisites, package signature/checksum, supported install command, config paths, and authentication flow. Record version and source, install as `ned` without sudo, and verify PATH in a fresh login. Defer Pi providers/models; configure no local endpoint. For HerdR, explicitly verify what “integration” means, its supported Hermes/Pi/Codex interfaces, process ownership, config/schema, and health check before installing. Do not install Claude Code.

## Phase 4 — VERIFY

Record sanitized command output and screenshots. Never capture tokens/passwords.

### Proxmox and guest hardware

On `pve1`, compare `qm config 10182` against the approved specification and use `qm status 10182`, `pvesm list datastore2`, and the GUI to verify VMID/name, resources, OVMF/q35, storage, controller, disk size/thin allocation, agent, NIC/no tag/firewall off, disk-only boot, and autostart. Use `qm agent 10182 ping` (or the installed version's supported guest-agent ping/API) and verify reported guest interfaces. Confirm no backup job selects 10182 and no unrelated VM/storage/network config changed by comparing preflight evidence.

### Inside the VM

- OS/desktop/time: verify `/etc/os-release` is the approved Ubuntu 26.04 point release, `hostnamectl`, `timedatectl`, `systemctl get-default`, a GNOME login/session, display resolution, reboot/login, and pending reboot/update state.
- Network/DNS: `ip address`, `ip route`, `nmcli connection show --active`, `resolvectl status`, gateway reachability, internal and external DNS lookups explicitly against both approved resolvers, outbound HTTPS, and duplicate-address monitoring. Confirm address remains after reboot.
- Accounts/security: `id glenn`, `id ned`, `sudo -n true` as `ned`, ownership/mode of sudoers drop-in, `sudo visudo -c`, strong interactive login for both users, no enabled root SSH, password SSH works, and public-key auth is not configured for either account. Verify effective SSH settings with `sshd -T`; do not expose password hashes.
- Services: `systemctl --no-pager --full status ssh qemu-guest-agent <NOMACHINE_SERVICE> <MESH_AGENT_SERVICE>` using discovered official service names; check listening ports and recent logs for errors. Test NoMachine from an approved client and confirm a usable GNOME session. Confirm the MeshCentral console shows the correct host online in the correct group and can perform one approved non-destructive inventory action.
- Browsers/automation: launch Chrome and Chromium in GNOME, record versions, open an HTTPS test page, and run a minimal pinned Playwright test as `ned` for each required engine. Confirm Playwright caches/browser files are owned by `ned` and there are no missing-library errors.
- Toolchains: in a fresh `ned` login, run `git --version`, `jq --version`, `rg --version`, `tmux -V`, `unzip -v`, `ffmpeg -version` if installed, `node --version`, `npm --version`, `type -a node npm`, `uv --version`, `uv python list`, `python3 --version`, and `gh --version`. Confirm Node is a supported LTS and npm resolves to the same installation; confirm system Python remains intact and `gh auth status` reports unauthenticated without initiating login.
- Agents: in a fresh `ned` login, use each official documented version/diagnostic command for Hermes, Codex, Pi, and HerdR. Verify executable provenance with `type -a`, user ownership, config/cache locations, and an approved harmless smoke test. Authentication tests are interactive and redacted. Confirm Pi has no provider/model selected, no local model endpoint exists, and Claude Code/Docker are absent. Validate HerdR's specifically approved integration and health check; if upstream documentation cannot define it, record it unverified rather than inventing a test.
- Containment: inspect `~/projects` ownership, all agent worktree locations, user PATH and package caches; check that no project or agent-created files are root-owned and no projects exist in `/root`, `/opt`, `/usr/local`, or the other user's home. Review privileged command history carefully without printing secrets.

Failures return to the relevant execution step; do not take the next snapshot until all checks for the current state pass.

## Phase 5 — CHECKPOINT

Before every snapshot, close/update applications, flush writes, confirm guest-agent health, and either shut the VM down cleanly (safest) or use a Proxmox-supported guest-agent-quiesced snapshot. Confirm `datastore2` supports snapshots and has headroom. Use only these approved names:

1. `hermes-2-01-ubuntu-clean` — Ubuntu Desktop installed, disk booting, hostname/network/accounts/SSH/guest agent minimally verified; before general updates.
2. `hermes-2-02-os-updated` — full OS updates applied, required reboot completed, services/network verified.
3. `hermes-2-03-dev-tools` — GNOME, Chrome, Chromium, Playwright, build/media tools, Node/npm, Python/uv, GitHub CLI, NoMachine, MeshCentral, and `~/projects` verified.
4. `hermes-2-04-hermes-codex` — verified official Hermes and Codex installs/authentication smoke tests; no secrets captured in snapshot notes.
5. `hermes-2-05-all-agents-working` — Pi and HerdR installed and all approved integration/smoke tests pass.

For the installed Proxmox version, confirm `qm snapshot 10182 <SNAPSHOT_NAME> --description '<SANITIZED_DESCRIPTION>'` in `qm help snapshot` before use. After each, list snapshots and record timestamp/config/disk state. Snapshots are rollback points, not backups; the “no backup job” requirement means they provide no independent recovery from storage loss.

## Rollback and recovery

- Before the OS install commits, stop and remove only VM 10182 if creation is wrong, after independently rechecking the VMID/name and enumerating its volumes. `qm destroy` is destructive and is intentionally not supplied as a copy/paste command; require explicit approval and use Proxmox's removal dialog/installed help so only VM 10182 and its newly allocated volumes are selected.
- After checkpoints exist, prefer a clean shutdown and Proxmox snapshot rollback to the immediately preceding approved snapshot. First record current state; rollback discards newer VM state and must be separately confirmed. Never roll back another VM or manipulate storage volumes directly.
- For network/SSH failure, use the Proxmox console, revert the NetworkManager connection or SSH drop-in, validate locally, and retest. Do not change `vmbr0`, host routes, DNS, or firewall to compensate for a guest error.
- For a broken agent/tool install, use its official uninstall/recovery procedure as the owning user, or revert to the preceding snapshot. Avoid ad-hoc deletion from `/usr` or another user's home.
- If datastore, ISO, or VM creation partially fails, stop; inspect `qm config 10182`, VM task logs, and `pvesm list datastore2`. Do not rerun allocation commands blindly because they are non-idempotent and can add duplicate disks.

## Risks, assumptions, and confirmation gates

- **Release drift:** 26.04.1 is current at authoring time, but execution must select the latest official 26.04 LTS Desktop point release and revalidate compatibility.
- **Storage semantics/capacity:** datastore type is unknown. Thin provisioning, discard, snapshot behavior, and required reserve must be confirmed; snapshots can exhaust the pool.
- **Network collision/exposure:** `/16`, untagged VLAN, address availability, DNS reachability, and inbound SSH/NoMachine/MeshCentral policy require network-owner confirmation. Password SSH and passwordless sudo materially increase risk.
- **Secure Boot:** pre-enrolled OVMF keys are proposed but not explicitly required. Confirm policy and compatibility with NoMachine/MeshCentral modules; do not silently disable Secure Boot to fix an agent.
- **Backup scope:** a cluster catch-all job may include the new VM. Exclusion may require a separately approved change.
- **Naming/upstream ambiguity:** Hermes, Pi, and HerdR identities and HerdR integration are unresolved hard gates. No packages may be selected by name alone.
- **Tool compatibility:** agent CLIs may require a different supported Node/Python range. Keep one Node strategy; if upstream requirements conflict, stop and revise rather than installing a second system-wide toolchain.
- **Resource/operations:** CPU `host` limits migration portability; 10 GiB may constrain simultaneous browsers/agents; autostart ordering is unspecified. Confirm host capacity and whether startup delay/order is needed.
- **Remote desktop:** GNOME/Wayland, headless resolution, NoMachine edition, and simultaneous local/remote sessions may interact. Test after reboot before agent deployment.
- **No independent backup:** snapshots on the same datastore do not protect against datastore failure; this is accepted only if the owner explicitly acknowledges it.

## Final self-review

- **Proxmox syntax:** command pattern uses only VMID 10182, quotes the semicolon boot order, allocates a separate EFI disk and 128 GiB SCSI disk, leaves VLAN absent, disables only the NIC firewall flag, and avoids host/storage/bridge/boot mutation. Remaining schema/backend uncertainty is deliberately gated by installed `qm help`, GUI/API review, and staged `qm config` checks.
- **Ubuntu 26.04:** official 26.04.1 availability/support was checked at authoring, but execution-day point release, ISO signature, package names, GNOME/Wayland, and third-party compatibility must be rechecked.
- **Node/npm:** one user-owned supported LTS strategy; no mixed apt/NodeSource/version-manager installs and no `sudo npm`. Agent engine constraints are checked before selection.
- **Root versus user / PATH:** sudo is limited to OS/services; projects, uv, Node, Playwright browsers, and agent CLIs belong to `ned`. Fresh-login `type -a`/version/ownership checks catch PATH shadowing and root-owned files.
- **Playwright:** pinned per project; dependency procedure is taken from the installed release; only system libraries use reviewed sudo. No `sudo npx` or root-owned cache.
- **HerdR:** upstream identity and integration are not assumed. Install/config/health checks are a hard execution-time documentation gate.
- **Credentials:** all secrets are placeholders and interactive; no passwords, tokens, API keys, SSH keys, or GitHub auth are scripted or logged. Snapshot descriptions and evidence are sanitized.
- **Idempotence:** VM disk/EFI creation, user creation, remote-agent enrollment, installer actions, and snapshots are explicitly treated as non-idempotent. Operators inspect state before retrying.
- **Infrastructure safety:** no command changes other VMs, storage pools, backup jobs, host networking/firewalls, or host boot settings. A discovered catch-all backup conflict or bridge/storage issue stops the run for separate approval.

Final acceptance requires every verification item to pass, all five approved snapshots to exist, credentials to remain undisclosed, all unresolved assumptions to be signed off, and the change record to show that no unrelated infrastructure changed.
