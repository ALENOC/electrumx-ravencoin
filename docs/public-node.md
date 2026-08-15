# Public node guide

Documentation: [Home](../README.rst) · [Docs index](README.md) ·
[Operations](operations.md) · [Troubleshooting](troubleshooting.md)

Finish private synchronization, indexing and read-only validation first. A
public endpoint is an operator choice, not part of release certification.

## Network prerequisites

- Assign the host a stable LAN address.
- Determine whether the ISP uses CGNAT; inbound forwarding may be impossible.
- Use dynamic DNS such as DuckDNS only after confirming the update process.
- Forward only the intended Electrum port, preferably TCP 50002.
- Keep Core JSON-RPC 8766 and unauthenticated Core REST private.

IPv6 can avoid some NAT problems, but firewall it deliberately and validate the
address from outside. Do not infer public reachability from a LAN test.

## TLS

Use a CA-issued certificate whose name matches the public hostname. The TLS
Compose overlay mounts `fullchain.pem` and `privkey.pem` read-only. Test from a
different network:

```sh
openssl s_client -connect node.example:50002 \
  -servername node.example -verify_return_error </dev/null
```

Renewal requires an ElectrumX restart so the process rereads the certificate.
Restart ElectrumX only; never restart Core to rotate TLS.

## External checklist

- DNS resolves to the current public address.
- TCP 50002 connects from outside the LAN.
- The certificate validates and matches the hostname.
- `server.version`, `server.features` and
  `server.ravencoin_backend` answer correctly.
- Core RPC and REST are not Internet-facing.
- The live validation checklist is complete before advertising the endpoint.
