# Lab network sharing (Mason Jar)

Mason Jar on a shared Windows compute server can saturate the NIC when many users run pipeline jobs against NAS-backed projects. Mason Jar **v3.2+** includes cooperative **adaptive bandwidth fair-share** so active pipeline jobs split available link capacity instead of one instance consuming 100%.

## What Mason Jar does (client-side)

- Tracks active heavy Python jobs machine-wide via `%ProgramData%\MasonJar\io-fairshare\registry\` (Windows). Registry heartbeats are best-effort: if antivirus or another user locks a registry file, fair-share may under-count active jobs briefly but Mason Jar will not crash.
- Project index refresh also registers (`project_index` for the full scan; `index_metadata` for standalone metadata probes) so opening a large NAS project shares the link with pipeline jobs instead of starving behind them.
- Each job’s NAS I/O is throttled to roughly `(link speed × 85% headroom) / number of active jobs`.
- When fewer jobs run, each job gets more bandwidth automatically.
- Files ≤256 KB skip throttling (project JSON, small metadata).
- Per-user toggle and link-speed override: **Start → Settings → Network**.

All lab members should run the same Mason Jar version for fair-share to apply to every instance.

## What the OS does not do

Windows and macOS do **not** fairly divide SMB file I/O between apps or RDP sessions. Mason Jar’s limiter is app-level, not a replacement for network infrastructure.

## Optional IT-level complements

These help the whole lab but are outside the Mason Jar installer:

- **Switch or NAS QoS** — prioritize or cap SMB traffic per VLAN/subnet.
- **Dedicated storage network** — separate NIC/VLAN for NAS vs internet.
- **NAS connection limits** — some appliances expose per-client throughput caps.

## Tuning on your compute server

1. Open Mason Jar on the server → **Start → Settings → Network**.
2. Under **Server network locations**, click **Select network drives…** and pick mapped drives or UNC share folders. Mason Jar stores drive/share **roots** (e.g. `Z:\` or `\\nas01\lab`) in machine-wide `config.json` so every RDP user sees the same list.
3. If auto-detect picks the wrong adapter, set **Manual link speed** to your NAS NIC (e.g. 1000 Mbps for 1 GbE).
4. Ensure `%ProgramData%\MasonJar\io-fairshare\` is writable by all RDP users (default on domain servers; IT may need to grant Modify on first deploy).

Shared `config.json` in that folder can also set machine-wide defaults (headroom, min Mbps per job) for all users. Advanced edits remain possible there; the Settings UI is the supported path for NAS location configuration.
