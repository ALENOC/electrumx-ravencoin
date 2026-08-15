# Hardware

Documentation: [Home](../README.rst) · [Docs index](README.md) ·
[Getting started](getting-started.md) · [Status](validation-status.md)

## Practical targets

| Platform | Recommendation | Evidence |
|---|---|---|
| Raspberry Pi 5 | 8 GB minimum, 16 GB preferred, NVMe and active cooling | Recommended target; runtime validation pending |
| Orange Pi 5-class | 8 GB minimum, 16 GB preferred, NVMe and active cooling | Recommended target; exact board validation pending |
| x86-64 mini-PC/NUC | 16 GB+, NVMe | Bundled amd64 path exercised |
| Dedicated server/VPS | 16 GB+, fast persistent SSD/NVMe | Suitable for public operation |

The table distinguishes a recommendation from a completed runtime validation.
Do not describe an SBC as fully validated until the live status document says so.

## Storage

Core raw blocks, `txindex`, `assetindex`, chainstate and the ElectrumX LevelDB
all write heavily during first synchronization. Use a reputable TLC or
enterprise-oriented SSD/NVMe with substantial free space. QLC can work but has
less endurance margin under long index workloads. A 1 TB drive is a reasonable
minimum starting point; 2 TB gives better long-term headroom.

Do not put chain or index data on microSD. Keep normal backups of configuration
and secrets, monitor SMART/NVMe health, and do not use swap as a substitute for
memory.

## Board notes

Raspberry Pi 5 needs active cooling, a reliable 5 V/5 A supply and a compatible
M.2 carrier. Prefer the documented PCIe Gen 2 setting for unattended use.
Orange Pi 5, 5B, 5 Plus and 5 Pro differ in M.2 layout and drive length; check
the exact board manual.

Boards around 1–2 GB RAM, including Orange Pi Zero-class systems, are not
appropriate for combined Core and ElectrumX indexing. Core-only use is a
different workload.
