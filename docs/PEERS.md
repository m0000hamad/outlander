# Managed peer servers

The **Peer servers / سرورهای مقابل** page saves SSH access, tests the connection,
and prepares an isolated tunnel on an Ubuntu/Debian server you control.

## Workflow

1. Open the console through HTTPS or a local SSH tunnel. To use the latter:

   ```bash
   ssh -L 18099:127.0.0.1:8099 root@YOUR_OUTLANDER_IP
   ```

   Open `http://127.0.0.1:18099/peers` locally and log in. Submitting SSH credentials
   over public plain HTTP is rejected. A TLS reverse proxy connected over loopback
   also works; do not expose that proxy's unencrypted upstream to other machines.
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

Authenticated managed REALITY/XHTTP tunnels explicitly allow loopback and RFC1918
destinations, so they can forward services on the peer's private network. The
core's default block on link-local/metadata destinations remains in place.

The original client configuration still connects to your Outlander IP and listen
port. This adds an encrypted server-to-server leg; it does not change or disguise
the client-to-Outlander leg.

## Isolation and recovery

- Direct, Auto, and the original Dokodemo gateway route are unchanged. New forwards
  each use their own `ol-peer-fwd-*` container; creating/removing them never restarts
  `ol-gateway`, `ol-tunnel`, or the panel. Auto does not silently choose a new peer.
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
