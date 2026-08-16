# Hardware guide

Documentation: [Home](../README.rst) · [Docs index](README.md) ·
[Getting started](getting-started.md) · [Operations](operations.md) ·
[Validation status](validation-status.md)

This guide helps choose a host for a combined Ravencoin Core and ElectrumX
node. A recommended target is not automatically a fully runtime-validated
configuration; current evidence is in [Validation status](validation-status.md).

## What the hardware is doing

The first synchronization is the demanding part. The host must:

1. write raw blockchain files and rebuild Core's chainstate;
2. maintain `txindex` for historical transaction lookup;
3. maintain `assetindex` for Ravencoin asset queries;
4. serve block data to ElectrumX through the private Core interface; and
5. let ElectrumX build and compact its own LevelDB history index.

That is a write-heavy database workload. Once synchronized, keeping up with
new blocks is much lighter, but the initial build still determines whether a
small host is pleasant to operate.

## Practical choices

| Platform | Practical starting point | Current wording |
|---|---|---|
| Raspberry Pi 5 | 8 GB minimum; 16 GB more comfortable; NVMe | Recommended low-power ARM64 target; bundled Core builds natively in CI and passes `make check` plus a basic smoke suite, not the full incident-specific certification |
| Orange Pi 5-class | 8 GB minimum; 16 GB preferred; NVMe | Recommended low-cost ARM64 target; same partial CI validation as Raspberry Pi 5 |
| x86-64 mini-PC/NUC | 16 GB or more; NVMe | Fastest and simplest bundled amd64 route |
| Dedicated server/VPS | 16 GB or more; persistent SSD/NVMe | Useful for a long-lived public node if raw TCP is available |

## Raspberry Pi 5

The Raspberry Pi 5 is the first low-power target to consider:

- choose 8 GB or more; 16 GB leaves more room for Core cache, ElectrumX cache
  and the operating system;
- use an NVMe SSD through a compatible M.2 carrier. Check the carrier's drive
  length and key before ordering; the official Pi 5 carrier variants do not
  all accept the same physical drive sizes;
- use active cooling. Initial indexing can keep the CPU busy for hours;
- use a reliable 5 V / 5 A USB-C Power Delivery supply;
- install a 64-bit Linux distribution with supported Docker and Compose;
- prefer the documented PCIe Gen 2 setting for unattended stability rather than
  assuming every Gen 3 drive/carrier combination is validated.

The bundled Core artifact is qualified for amd64. `docker compose up -d
--build` on a Raspberry Pi 5 builds and runs an ARM64 Core image directly,
from the same certified source identity as the amd64 build. That ARM64 build
has passed on native ARM64 GitHub Actions hardware (`make check` plus a
startup/RPC/REST/txindex/restart smoke suite; run IDs and hashes in
[Validation status](validation-status.md)), but has not been run through the
incident-specific consensus probes that back the amd64 status, and no report
is persisted in this repository. A physical Raspberry Pi 5 deployment has
since executed the documented SSD procedure end to end: local ARM64 build
(about 16 minutes observed, with cached dependency layers), Core and
ElectrumX startup and healthchecks, RPC identity checks and binary hashes
matching the native ARM64 CI build; full incident-specific consensus
qualification is still pending. Existing-Core mode remains available for
operators who already manage a separate, non-pruned Core; using it does not
by itself make an unreviewed Core release safe.

For a complete walk-through that keeps the operating system on the microSD
and puts Docker, the blockchain and the ElectrumX database on a USB SSD or
an M.2 NVMe drive, see the Raspberry Pi 5 section of the repository
[README](../README.rst).

## Orange Pi 5 family

An Orange Pi 5-class RK3588/RK3588S board with 8 GB or more, NVMe, active
cooling and a reliable power supply can be a useful low-cost target. Verify the
exact model before buying storage or writing boot instructions:

- Orange Pi 5, 5B, 5 Plus and 5 Pro differ in M.2 connector, PCIe lanes,
  supported drive length and boot behavior;
- follow the vendor documentation for the exact board and OS image;
- check that the board's thermal solution can sustain a long index build;
- do not assume an accessory or image for one family member applies to another.

The bundled Core image builds and runs the same way on the Orange Pi 5 family
as on the Raspberry Pi 5, from the same pinned ARM64 source path described
above, with the same partial CI validation and the same gap versus full
qualification. The Orange Pi Zero 3 and other
roughly 1-2 GB boards are not suitable for a combined Core plus ElectrumX
workload regardless of architecture support. They may be useful for a
different, Core-only experiment, but low memory and swap pressure make them a
poor choice for a node that must also build the ElectrumX index.

## x86-64 mini-PC or NUC

An x86-64 mini-PC with 16 GB or more and a fast NVMe drive is the easiest
starting point. It has the broadest binary compatibility and the shortest
initial indexing path among the recommended choices. More cores reduce the
one-off wait, but storage latency, cooling and free space still matter.

## Server or VPS

A dedicated server or VPS can be a good public-node host when it offers:

- persistent local SSD/NVMe rather than temporary or network-block storage;
- enough memory for Core, indexes and ElectrumX;
- reliable disk I/O and a way to monitor capacity and health;
- inbound raw TCP or a reviewed TCP relay design;
- a backup and recovery plan that does not depend on deleting live data.

Do not assume a generic HTTP reverse proxy or HTTP-only tunnel can carry
Electrum's raw TCP/TLS protocol. Confirm the provider's transport semantics.

## Storage: why NVMe matters

The workload has several layers:

```text
raw blocks -> chainstate -> txindex + assetindex -> ElectrumX LevelDB index
```

Each layer adds reads, writes and compaction. HDD-only operation is not
recommended for the initial build, and microSD is a poor location for chain or
index data because of latency, write endurance and failure recovery.

Prefer a reputable TLC SSD/NVMe or an enterprise-oriented drive. QLC can work,
but has less endurance margin during long sustained writes. Keep free-space
headroom instead of filling a drive to its nominal capacity. Actual chain and
index sizes grow, so measure your deployment rather than relying on a fixed
number in this document.

## Memory and swap

8 GB is a practical minimum for a combined SBC deployment; 16 GB is more
comfortable and preferable when affordable. On x86, 16 GB or more gives a
better margin for the OS, Core and ElectrumX. Swap can prevent an immediate
out-of-memory kill, but it is not a replacement for RAM and can make an
already-busy index painfully slow.

## Power, cooling and maintenance

Use active cooling on small boards, a stable supply, and a host that can remain
powered for the initial synchronization. Watch temperatures, disk utilization,
SMART/NVMe health counters and filesystem errors. Enable normal Linux TRIM
where supported. Keep the host ventilated and do not place it where a fan or
drive can collect dust and heat.

## What is and is not validated

The project can certify a Core software release with bounded deterministic
fixtures. That does not prove that every SBC, carrier, power supply, kernel and
storage combination will complete a live mainnet index. Live platform status,
asset RPC readiness and ElectrumX catch-up are intentionally tracked separately
in [Validation status](validation-status.md).
