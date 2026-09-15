# KIM-SERVER nonpaged `Proc` pool — handoff

**Last updated:** 2026-07-08  
**Host:** KIM-SERVER (Puget Rackstation, Win11 Pro **22621.4317**, 127 GB RAM, lab NAS on `Z:`)  
**Constraint:** Reboot clears accumulated pool debt (~600-mile trip to power-cycle). Do not block the lab for idle experiments.

---

## Problem

After **4–7 days** uptime the server becomes globally slow. Task Manager does not show the cause: **nonpaged kernel pool**, especially the **`Proc` tag** (`EPROCESS` objects).

| Signal | Meaning |
|--------|---------|
| ~300k `Proc` outstanding vs ~400 **live** processes | ~299k **zombie `EPROCESS`** |
| `Proc_frees` ≈ **0 from ~0.3 h after reboot** | Kernel **does not return** process objects on this host |
| RDP sign-out, kill Python, idle | **No drop** in `Proc_MB` |
| Only reboot | Clears debt |

**Two coupled effects:** (1) broken kernel teardown (primary); (2) process-create rate from lab workload fills the pool faster on a no-free kernel (secondary).

---

## Conclusions (2026-07 investigation)

1. **Windows-first diagnosis** — not an app log / Mason Jar gate problem. Generic isolation test: 500 × `cmd.exe /c exit` → **`Proc_out` +1,443**, **`Proc_frees` unchanged (8)**.
2. **Mason Jar v6.0.12+ worker** (supervisor + [`py/masonjar_worker.py`](../py/masonjar_worker.py)) is **shipped** and correct app hygiene; it **did not** materially change system `ΔProc_out/hour` (~2700–3000/h).
3. **Do not** hobble Mason Jar parallelism (shared worker rejected). **Do not** install new third-party products to fix the leak.

---

## Host inventory (2026-07-08)

### RDP Wrapper (sebaxakerhtc)

| Item | Value |
|------|-------|
| Path | `C:\Program Files\RDP Wrapper\` |
| Diagnostic | **Installed**, **Running**, **Listening**, **[fully supported]** |
| termsrv | **10.0.22621.4249** |
| Settings | RDP port 3389; single session per user **on** |

Concurrent RDP on Win11 Pro via patched Termservice remains a suspect for session/process churn; wrapper self-test passes.

### ZEISS ZEN + Sentinel LDK

| Item | Value |
|------|-------|
| **ZEISS ZEN 3.4 (blue edition)** | Installed; **runs 24/7** — major image-processing workload |
| **`hasplms` service** | Running (Sentinel LDK License Manager) |
| Runtime | `C:\Program Files (x86)\Common Files\Aladdin Shared\HASP\` (files **2020**) |
| License model | **Network license server** (not USB dongle) |
| **`aksdf.sys`** | Sentinel minifilter — **required for ZEN**; **do not remove** |

### `fltmc` (elevated)

**`aksdf`** altitude **145900**, instances on **`C:`**, **`D:`**, shadow copies, **`\\Device\Mup`** (SMB / `Z:`). Also on Mup: FileInfo, UCPD, WdFilter, bfs.

Driver link date **2016** — very old for Win11 22621.

### Other context

- Multi-user RDP (Matt, John, Devin); long **Disconnected** sessions add churn.
- Mason Jar: frequent process spawner; heavy `Z:` I/O.
- Windows hotfixes last noted **2025-01-22** on build 22621.4317.

---

## Operator next steps (Matt / IT — not Mason Jar releases)

| Priority | Action |
|----------|--------|
| 1 | **Zeiss support:** ZEN 3.4 + Win11 22621 + network licensing — ask for **supported Sentinel LDK runtime/driver update** (not removal) |
| 2 | **RDP hygiene:** sign out idle users instead of long disconnect; optional maintenance test with wrapper disabled |
| 3 | **Windows + GPU drivers:** cumulative updates on maintenance reboot |
| 4 | **Reboot cadence:** every **5–7 days** if unresolved, before `Proc_MB` ~1000 |
| 5 | **Maintenance trip:** poolmon.exe (WDK on laptop) + optional kernel debug; Microsoft case if inconclusive |

**Hard rules for Mason Jar agents:** Document host findings only. Never install Sentinel/HASP/FlexNet or other third-party products as a Mason Jar fix. Never run `m465_*` repro scripts on KIM-SERVER.

---

## Mason Jar app scope (closed)

| Item | Status |
|------|--------|
| Per-job Python spawn | **Fixed** — v6.0.12+ worker ([`src/python_job.ts`](../src/python_job.ts)) |
| Align/Adjust orphan hardening | **Shipped** (6.0.13) |
| Why kernel never frees `Proc` | **Host** — outside app |
| Clear Proc debt | **Reboot only** |

Fallback: `MASONJAR_PYTHON_WORKER=0` restores one-process-per-job (debug only; does not fix host leak).

---

## References

- [EPROCESS (Microsoft Learn)](https://learn.microsoft.com/en-us/windows-hardware/drivers/kernel/eprocess)
- [PoolMon kernel leak debugging](https://learn.microsoft.com/en-us/windows-hardware/drivers/debugger/using-poolmon-to-find-a-kernel-mode-memory-leak)
- [Zeiss HASP licensing troubleshooting](https://knowledge.zeiss.com/rms/en/arivis-pro/licensing-setup/hasp-license-troubleshooting)
