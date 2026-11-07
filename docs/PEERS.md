# Managed peer servers

The **Peer servers / سرورهای مقابل** page saves SSH access, tests the connection,
and prepares an isolated tunnel on an Ubuntu/Debian server you control.

## Workflow

1. Open the console at its HTTP or HTTPS address and log in. Peer SSH connections
   do not depend on the console's web transport. To optionally encrypt the browser
   connection using a local SSH tunnel:

   ```bash
   ssh -L 18099:127.0.0.1:8099 root@YOUR_OUTLANDER_IP
   ```

   Open `http://127.0.0.1:18099/peers` locally and log in. Direct authenticated HTTP
   submissions are also accepted. Plain HTTP does not encrypt browser-to-console
   credentials; remote SSH authentication and host-key checking remain enabled.
2. Save a name, IP/hostname, SSH port, root username, and **either** a password
   **or** an unencrypted private SSH key. Test SSH and check the displayed host-key
   fingerprint against the peer's console. The first key is pinned; subsequent
   changes are rejected. Replace a server entry only after independently checking
   an intentional host-key change and removing its managed forwards/services.
3. Choose **SSH**, **VLESS + REALITY**, or **VLESS + XHTTP + REALITY**, then prepare
   the tunnel. SSH needs no remote Docker service and supports TCP only. The two
   REALITY transports install Docker from the OS repository only if it is absent,
   then create a separately named Xray container. Default remote ports are 24443
   and 24444 respectively. Override the port/SNI before first preparation if needed.
   Allow that TCP port in the provider firewall too. Preparation runs in the
   background; results appear on the server card. Network timeouts can be retried.
4. In **Forwarding**, choose **Managed peer tunnel**, the server, prepared transport,
   local listen port, and destination `IP:port` (or `hostname:port`). The destination
   is dialled **from the peer server**. SSH forwards require `tcp`; REALITY/XHTTP
   accept `tcp`, `udp`, or `both`. Managed forwards require a single port and listen
   on all local addresses. Existing listen ports cannot be reused.

## Automatic peer selection

**Auto** can select prepared REALITY, XHTTP and SSH peers without choosing a
specific server manually. Direct is excluded. TLS-camouflaged encrypted REALITY
and XHTTP share the preferred tier; encrypted SSH is the next tier (TCP only).
Dokodemo remains the final compatibility fallback; its encryption depends on the
gateway outbound and is not guaranteed by the Dokodemo inbound itself. This is
an explicit transport policy, not a measured security score or filtering guarantee.
Within a tier, choose the fastest healthy candidate using the mean of two actual
HTTP(S) plus destination CONNECT checks, excluding local container startup time.
Keep a healthy current route unless the same-tier replacement improves by both
20% and at least 50 ms. A failed route or higher-tier candidate switches immediately.
Displayed milliseconds are check duration, not throughput or application ping.

REALITY/XHTTP candidates are checked using private, temporary SOCKS probe
containers bound to loopback. A real HTTP/HTTPS response through the transport
is required, using the inventory's existing `settings.health_check` URL, followed
by a destination TCP CONNECT check. The gateway also needs a real HTTP response.
SSH candidates also require actual HTTP(S) traffic through their authenticated
dynamic forward. Each candidate must pass both samples to be eligible.
These are transport/TCP health checks; they do not authenticate the application's
VLESS credentials or independently verify UDP delivery.

Only profiles already marked ready are considered; Auto never installs or changes
the remote server. Active rules show the selected peer name and transport.
Switches use the rule editor's rollback path. If every candidate fails, the last
selection remains configured with an error status, except an old Auto Direct
selection is disabled (pending) to enforce the new no-Direct policy. Probes are removed after each
check; interrupted probes are cleaned on console restart. Fixed rules are not
converted to Auto or changed by these checks.

Authenticated managed REALITY/XHTTP tunnels explicitly allow loopback and RFC1918
destinations, so they can forward services on the peer's private network. The
core's default block on link-local/metadata destinations remains in place.

The original client configuration still connects to your Outlander IP and listen
port. This adds an encrypted server-to-server leg; it does not change or disguise
the client-to-Outlander leg.

## Isolation and recovery

- Fixed Direct and Dokodemo rules are unchanged. New managed forwards
  each use their own `ol-peer-fwd-*` container; creating/removing them never restarts
  `ol-gateway`, `ol-tunnel`, or the panel. Auto can select prepared peers as described above.
- Remote containers are named `ol-peer-<installation-id>-<peer-id>-<transport>`.
  Configuration is under `/etc/outlander-peers/<installation-id>/...`. A busy remote
  port or a container without the matching ownership label causes setup to stop.
  Preparing an already-ready tunnel does not replace its keys/config or restart it.
- SSH forwards reconnect after console restart or a dropped SSH session. The
  dedicated Xray containers use Docker's `unless-stopped` restart policy.
- Credentials, private keys, and generated tunnel configuration are kept under
  `data/peers/` with private file/directory permissions. They are not encrypted
  against server-root access, are never returned by the page API, and are included
  in private backups. `data/` is excluded from Git. Protect backup archives too.
- Remove associated forwarding rules before removing a remote tunnel service;
  remove all remote services before deleting the peer's saved credentials. Remote
  services are removed only by their explicit button, never during local uninstall.
  The remote UFW allow rule and private config directory are retained after service
  removal so existing administrator firewall policy is not deleted accidentally.
- Local uninstall removes only managed containers bearing this installation's
  ownership label. `--purge-data` also removes the saved peer credentials locally.

Rebuild the console image after updating from a version without SSH management:

```bash
docker compose build console
docker compose up -d --no-deps console
```

This updates the console only; it does not restart the gateway, panel or tunnel.


## OpenVPN (phase one)

Rebuild and recreate **console** once after updating; the console image now
includes OpenVPN, iproute2 and Xray. Existing gateway/panel services need no restart:

```bash
docker compose build console
docker compose up -d --no-deps console
```

**Existing VPN / imported profile:** on Peer servers, choose a `.ovpn` file or paste its
contents in **OpenVPN / Import profile**, give it a name and save. The profile
must contain inline `<ca>` plus `<cert>`/`<key>`, or an inline CA plus
`auth-user-pass` with credentials entered in the separate fields. External file
paths, scripts, plugins, interactive encrypted private keys, compression, TAP,
static-key-only VPNs and arbitrary OpenVPN options are rejected. Host routing,
DNS-push and redirect-gateway directives are discarded. This phase supports
IPv4 TUN connections over UDP or TCP; managed servers use UDP.

**New remote VPN / external:** save the remote root SSH credentials, choose
OpenVPN and prepare. Ubuntu/Debian OpenVPN and iptables packages are installed;
an independent `openvpn-server@ol-<instance>-<peer>` systemd service receives a
private CA, separate client/server certificates and tls-crypt key. Certificates
last 825 days; remove forwards/service and prepare again before expiry to rotate
them. Preparation retries retain the same pending credentials. UDP defaults to
1194; choose another free port if needed and allow it in provider firewalls.
The script enables persistent IPv4 forwarding and installs only tagged rules for
its own VPN interface/subnet. Server removal disables only its own service and
removes its exact active firewall rules; saved configuration and the shared
forwarding sysctl are retained. It does not install Docker remotely or change
the remote default route. SNI does not apply to OpenVPN.

**Forwarding:** choose Managed peer tunnel, select the imported profile or remote
server, then OpenVPN. TCP, UDP and both are supported. Use a destination reachable
inside that VPN (for example the remote VPN address or a reachable private IP),
not `127.0.0.1`/`localhost`. A service listening only on remote loopback must also
listen on its VPN/private address. Existing client links continue to use the
Outlander address and chosen listen port. OpenVPN is manually selected in phase
one; Auto retains its existing transport policy.

Each forwarding rule runs its own OpenVPN session and Xray listener in a Docker
bridge network namespace with `/dev/net/tun` and NET_ADMIN. The container receives
only its own private credentials and runtime script, not the Docker socket or
full repository. Marked Xray sockets use a private VPN routing table with a
prohibit fallback; the host default route, SSH and other tunnels stay unchanged.
IPv6 marked traffic is prohibited. Domain names are resolved with the container's
normal DNS resolver; use destination IPs if DNS must remain inside the VPN.
VPN-provided private destinations are explicitly allowed by Xray; link-local
metadata destinations retain the core default restriction.

Creating a forward waits for the VPN and listener, and removes the new container
if startup fails. Imported entries marked ready mean configured, not independently
verified connectivity. Docker restarts persistent forwards after a reboot or
process failure. Disconnects cannot fall back to direct egress. An existing VPN
must permit simultaneous sessions if you use the same profile in multiple rules;
managed servers permit this. Credentials stay private under `data/peers/`, are
excluded from Git, and are included in private backups. Delete associated forwards
before deleting the profile or removing its remote service.


## Internal OpenVPN on this server

The **Internal OpenVPN / OpenVPN داخلی (اینترنال)** card appears at the top of
both Peer servers and Forwarding. Check **Enable OpenVPN on this server**. Setup
runs in the background, the page refreshes while it is working, and reports
**Ready** only after a real HTTP response traverses an authenticated OpenVPN
session. No external profile, remote SSH server or manual certificate setup is
needed. Failures appear on the card and can be retried with the Apply / Retry
button, preserving existing credentials.

In Forwarding, select **Internal OpenVPN** directly in the mode selector, then
enter the destination, local listen port and TCP/UDP/both. Existing internal
rules carry the same explicit label and can be edited like other forwards. The
mode is unavailable until local setup succeeds. This is a local VPN hop:
traffic still exits through this server; it does not create a remote egress IP.
Use destination IPs reachable from this server; loopback destinations are not
supported by this mode.

The server and each forwarding client run in separate container namespaces on a
private Docker bridge. The VPN UDP port is not published on the host or opened
to the Internet. Server IPv4 forwarding and NAT are confined to that namespace;
no host route, default gateway, sysctl, system OpenVPN service or remote SSH
configuration is changed. Only configured forward listen ports are published.
A private health responder binds only to the VPN address and verifies setup
without depending on an external website. The tunnel uses mutual TLS, tls-crypt
and generated per-installation credentials.

Uncheck the option to disable the local service. Existing dependent forwards
must be removed first; a rejected disable leaves the service running. Disabling
retains private credentials and the empty owned bridge for reactivation. Docker
restarts enabled containers after a reboot. After restoring settings on another
server, enable/retry Internal OpenVPN before recreating its forwards. Auto does
not select this local hop. Runtime credentials remain part of private backups.
