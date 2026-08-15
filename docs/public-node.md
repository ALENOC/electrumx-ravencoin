# Public node guide

Documentation: [Home](../README.rst) · [Docs index](README.md) ·
[Getting started](getting-started.md) · [Operations](operations.md) ·
[Troubleshooting](troubleshooting.md)

Publishing is optional. Finish a private, synchronized node first. A public
endpoint adds DNS, router, ISP, certificate and monitoring responsibilities;
none of those should be used to hide an unhealthy Core or ElectrumX index.

## Private versus public

| Mode | What it means | Networking needed |
|---|---|---|
| Private | Your wallets use the server on your host, LAN or VPN | No router forwarding |
| Public | Other wallets can connect to the server | Stable hostname, inbound TCP, TLS and external checks |

Get the private mode working first. You can add public networking later without
changing the chain data or wallet keys.

## The public path

```text
dynamic/public IP
        |
        v
DuckDNS or your DNS provider
        |
        v
home router
        |
   TCP 50002
        |
        v
ElectrumX TLS listener
```

Core JSON-RPC and Core REST are not part of this path. Keep port 8766 private;
REST is unauthenticated. Do not forward ElectrumX management RPC on 8000 or an
unencrypted public Electrum listener on 50001.

## Dynamic DNS and DuckDNS

### My home IP changes — what do I do?

DNS is the directory that maps a hostname to an IP address. Dynamic DNS (DDNS)
updates that mapping when your residential public IP changes. For example,
`my-ravencoin-node.duckdns.org` can remain the name clients use while the
address behind it changes.

DuckDNS is a free example provider. Its official [HTTP API specification](https://www.duckdns.org/spec.jsp)
supports an HTTPS update request with a subdomain, token and optional IPv4,
IPv6 and TXT values. The provider's current response is `OK` or `KO`, with
`UPDATED` or `NOCHANGE` in verbose mode. Check the provider documentation before
copying commands because service details can change.

The important relationship is:

```text
public IP changes
        |
        v
DuckDNS updater calls the provider over HTTPS
        |
        v
my-ravencoin-node.duckdns.org stays current
```

### Account and hostname

1. Open [DuckDNS](https://www.duckdns.org/) and sign in using one of its
   currently offered identity providers.
2. Create a subdomain, using the subname only, such as
   `my-ravencoin-node`. The full name becomes
   `my-ravencoin-node.duckdns.org`.
3. Treat the account token as a password. It can update your DNS record.
4. Never put the token in Git, a README, a shell command copied into a public
   issue, or a process list.

The setup script has an optional implementation for this repository:

```sh
./setup.sh --configure-ddns
```

It asks for the subname and reads the token without echoing it. The repository
stores the token under `.secrets/duckdns_token` with restrictive permissions;
`.secrets/` is Git-ignored. It installs a user service and timer that run the
repository's updater after boot and periodically. If user services must run
before login, enable user lingering for the operator account and inspect the
resulting units:

```sh
systemctl --user list-timers electrumx-ravencoin-ddns.timer
systemctl --user status electrumx-ravencoin-ddns.service
```

The updater is separate from ElectrumX. A DuckDNS outage or expired token makes
the hostname stale; it must not stop the wallet-query service. It should fail
without logging the token and try again on its next timer run.

If you do not use the setup helper, use the provider's official updater or
provider-supported router integration. A safe generic API shape is:

```text
https://www.duckdns.org/update?domains=SUBNAME&token=TOKEN&ip=
```

Do not paste a real token into a shell history. Store it in a protected file and
have the updater read that file. Leave the IPv4 `ip` value empty only when you
intend DuckDNS to detect the address of the request; configure IPv6 explicitly
when your setup needs it.

### Verify the name

After the first update, compare DNS with the router's WAN address:

```sh
dig +short my-ravencoin-node.duckdns.org
getent hosts my-ravencoin-node.duckdns.org
```

The hostname must resolve to the address the Internet should use. A correct DNS
answer does not prove that packets can reach your host.

## Carrier-grade NAT (CGNAT)

CGNAT means the ISP shares one public IPv4 address among multiple customers.
DuckDNS can point at that shared address, but it cannot create an inbound path
through the ISP's NAT.

Compare:

1. the address shown by the router as its WAN/Internet address; and
2. the address returned by an external service or the DNS answer for your
   hostname.

If the router WAN address is private (`10.0.0.0/8`, `172.16.0.0/12`, or
`192.168.0.0/16`) or in the carrier range `100.64.0.0/10`, while the external
address is different, you are probably behind CGNAT. This is a useful signal,
not a universal proof; ask the ISP when uncertain.

Possible solutions are:

- ask the ISP for a public IPv4 address;
- use usable public IPv6 with a carefully configured firewall and AAAA record;
- use a VPS or relay design that explicitly carries raw TCP/TLS.

An ordinary HTTP-only tunnel is not equivalent. Electrum is a line-oriented
protocol over raw TCP or TLS; an HTTP tunnel does not automatically transport
it.

## Stable LAN address

A router forwarding rule must point at the node's local address. Configure a
DHCP reservation keyed to the node's MAC address, which is usually easiest, or
configure a static address outside the DHCP pool. The address below is only an
example:

```text
node on the LAN: 192.168.1.50
```

After a reboot, verify the node still has the reserved address before testing
the forward. Do not hard-code this example address into the project files.

## Port forwarding

The router rule should be as narrow as possible:

```text
Internet
   |
public hostname / IP
   |
home router
   |
TCP external 50002 -> TCP 50002
   |
192.168.1.50 (example node address)
   |
ElectrumX TLS
```

In the router UI, look for **Port forwarding**, **Virtual server** or **NAT**:

| Field | Value |
|---|---|
| Protocol | TCP |
| External port | 50002 |
| Internal address | the node's reserved LAN address |
| Internal port | 50002 |

Do not use UPnP as a substitute for an explicit rule. Never forward:

- `8766`: Core JSON-RPC and unauthenticated REST;
- `8000`: ElectrumX management RPC;
- `50001`: unencrypted Electrum.

## TLS certificates

TLS encrypts the wallet/server connection and lets the wallet verify that the
certificate name matches the hostname it intended to reach. Obtain a
CA-valid certificate for the exact public hostname, such as
`my-ravencoin-node.duckdns.org`. The repository does not issue certificates;
use a current ACME client/provider integration such as Certbot or lego and
follow that tool's current documentation.

The Compose TLS overlay expects a directory containing:

```text
fullchain.pem
privkey.pem
```

Keep `privkey.pem` non-world-readable and mount the directory read-only. Set
the hostname and certificate directory in `.env`, then validate the Compose
model before enabling the overlay:

```sh
docker compose -f compose.yaml -f compose.tls.yaml config --quiet
docker compose -f compose.yaml -f compose.tls.yaml up -d
```

The host name in the certificate, the host name in `.env` and the host name
used by clients must match. A certificate for an IP address is not a substitute
for a certificate for the DNS name clients use.

### Renewal

Certificates expire. Configure the ACME client's deploy/renewal hook to make
ElectrumX reread the renewed files. The hook should restart ElectrumX only;
Core does not need restarting for certificate rotation and must not be
restarted during synchronization or reindexing.

For example, after reviewing the paths for your host:

```sh
docker compose restart electrumx
```

Test the hook before the first expiry. Keep the certificate directory and hook
outside Git, and do not log the private key.

## Test from outside the LAN

A LAN connection can work even when NAT, firewall or DNS is wrong. Test from a
phone on mobile data, another site or a remote host:

```sh
openssl s_client \
  -connect my-ravencoin-node.duckdns.org:50002 \
  -servername my-ravencoin-node.duckdns.org \
  -verify_return_error </dev/null
```

Look for a successful certificate verification and the expected hostname. DNS
resolution, TCP connection and TLS verification must all work. Then use the
project's supported Electrum client or a small protocol check to confirm that
`server.version`, `server.features` and `server.ravencoin_backend` answer.

## Public-node checklist

- [ ] Core is synchronized and its required indexes are usable.
- [ ] ElectrumX has completed its historical index.
- [ ] The hostname resolves to the current public address.
- [ ] CGNAT has been ruled out or a raw-TCP alternative is working.
- [ ] The node has a stable LAN address.
- [ ] Only TCP 50002 is forwarded to the node.
- [ ] Core RPC and REST are not Internet-facing.
- [ ] The certificate is CA-valid, current and matches the hostname.
- [ ] Renewal is automated and restarts ElectrumX only.
- [ ] TLS and the Electrum handshake work from outside the LAN.
- [ ] Live backend, checkpoint, asset and chain validation gates are complete.

The live status document remains authoritative; this checklist is a procedure,
not evidence that any particular public endpoint is currently validated.
