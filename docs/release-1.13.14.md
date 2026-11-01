# ElectrumX-RVN 1.13.14

ElectrumX-RVN 1.13.14 makes fresh monitor deployments recover automatically when
Docker starts before a published LAN address is available.

## Changes

- Fresh installations with the monitor selected automatically enable
  `electrumx-ravencoin-monitor-start.service` for boots and Docker restarts.
- The service reads published addresses from the selected Compose project and
  overlays, retries every 10 seconds, and recreates only the monitor to clear
  incomplete Docker endpoints. Core and ElectrumX are not restarted.
- The startup helper is pinned with the monitor bundle, copied to a root-owned
  location and verified against the authenticated bundle before enabling the
  service. It runs as the installing user's UID/GID; setup requires sudo.
- Partial startup-service installation is removed during fresh-install rollback.
  Existing startup files are preserved by refusing to overwrite them.
- The monitor pin includes delayed-address regression tests and CI recovery proof.

## Compatibility

No protocol, database format or Core identity changes. The optional privileged
bandwidth/connection controller remains disabled unless explicitly selected.
Monitor recreation resets history held in RAM.

The installer automatically provisions recovery on fresh installations. An
ordinary update preserves the existing installation and does not retroactively
install a host startup unit. Existing deployments must explicitly install the
startup service using the monitor deployment instructions and their complete
Compose file list. Disable any previous recovery unit before replacing it to
avoid duplicate startup owners.

## Verification and qualification

Version `1.13.14`, artifact revision `0`, retains the current offline Ed25519
release/update trust root, key ID `6f4f944c9b0a19a1`. Verify the manifest signature,
artifact digests and provenance before deployment.

Hardware qualification is **PENDING**, as recorded in
[the qualification plan](HARDWARE_QUALIFICATION_1.13.14.md). Automated test results
must not be described as qualification of the signed artifacts on real hardware.
