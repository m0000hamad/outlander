# Changes

## Console 1.6.0 — migration backup and forwarding loop guard

- Add an authenticated Backup / Restore page for outbound settings, forwarding rules and peer
  server state. Verify the archive manifest and file hashes, reject unsafe archive entries, stage
  and validate before replacing files, and cap uploads at 64 MB.
- Re-render Xray, sing-box and nftables after restore, recreate local managed-forward containers,
  apply forwarding rules, and restart the running gateway/tunnel services. Preserve the remote
  peer servers; restoring never sends commands to them.
- Include SSH peer credentials in the archive and label the file as sensitive. Keep panel user
  database and TLS certificates outside this settings-only archive.
- Refuse new same-port forwards whose target resolves to a local host address. This prevents the
  self-forward rules that filled conntrack and exhausted the server's RAM.

## Console 1.5.1 — gateway forwarding loop fix

- Keep the original SOCKS destination when sniffing TLS SNI or HTTP Host. Use
  sniffed names for routing only. Previously a client's domain resolving back
  to the forwarding listener could recursively connect gateway -> listener ->
  gateway, exhausting conntrack and making SSH/console inaccessible.
- Add a real Xray SOCKS + TLS ClientHello regression with separate intended and
  SNI-resolved loopback destinations; no public network or server changes needed.

## Console 1.5 — ranked automatic tunnels

- Exclude Direct from new automatic selections. Rank prepared REALITY/XHTTP in
  one preferred tier, SSH (TCP only) next, and retain Dokodemo as the final fallback.
  Dokodemo's encryption is determined by its gateway outbound, not its inbound.
- Measure two successful HTTP(S) and destination CONNECT checks per candidate;
  exclude probe-container startup from timing. Choose minimum mean check duration
  within each tier; this does not measure bandwidth or prove censorship resistance.
- Keep a healthy current same-tier route unless improvement meets both 20% and
  50 ms. Failed current paths and higher-priority tiers switch without that delay.
- Show selected check milliseconds in Active rules. Keep fixed rules and existing
  rollback behavior; all-failed checks retain the prior tunnel with an error.
  Existing Auto Direct rules become pending when no tunnel is healthy, so an
  upgrade cannot silently keep sending automatic traffic over Direct.

### SSH setup diagnostics

- Distinguish pre-login SSH handshake failures, authentication failures, refused
  or unreachable SSH ports and remote tunnel-port conflicts. Return fixed,
  bilingual messages without exposing remote stdout, stderr or credentials.

## Console 1.4 — automatic prepared-peer failover

- Extend Auto to prepared REALITY, XHTTP and compatible SSH peers. Prefer direct,
  retain a healthy current peer, try other prepared peers, and keep the original
  Dokodemo gateway as a final fallback. Support peer-side DNS when local DNS fails.
- Verify actual HTTP/HTTPS traffic through loopback-only temporary probe containers;
  a local SOCKS acknowledgement alone is insufficient. Clean probes after checks
  and recover interrupted probes after console restart. No remote installs occur.
- Preserve mode=auto while tracking the active peer/server/runtime separately.
  Display the selected server and transport without making editor forms stale.
- Reuse rollback-capable deployment for automatic switches, including owned peer
  container creation/removal. Preserve the previous route on failed deployment or
  when no healthy route is found. Leave fixed forwarding rules unchanged.

### Auto route visibility

Active rules now explicitly display both automatic mode and the selected path
(Direct or Dokodemo), instead of displaying the resolved path as though it were
the configured fixed mode. Pending/error states keep the Auto indication too.

## Console 1.3 — edit active forwarding rules

- Add Edit/Save and apply/Cancel on each Active rules row in Persian and English.
  Prefill protocol, mode, listen IP/port, destination, peer server and transport.
- Replace the rule at its existing position; no delete/re-add is necessary.
  Re-evaluate Auto on edit and reject unreachable routes without discarding the
  current rule. Reject stale editor submissions and overlapping/busy ports.
- Restore saved configuration and affected runtime if applying the replacement
  fails; explicitly report a recovery error if restoration itself fails.
- Reload the shared Dokodemo process only when its inbounds change. Direct-only
  and peer-only edits leave that process alone; peer edits replace only that
  rule's owned container. The gateway and panel are never restarted by an edit.

## Console 1.2 — managed peer port tunnels

- Add bilingual Peer servers page with root SSH password/private-key registration,
  connection test, pinned host keys, background setup status, transport removal,
  and credential deletion. Authenticated credential submission supports HTTP and HTTPS;
  remote SSH authentication and host-key checking are independent of web transport.
- Automatically prepare SSH/TCP, VLESS/REALITY and VLESS/XHTTP/REALITY transports on
  Ubuntu/Debian peers. Isolate remote services by installation, peer and transport;
  validate generated Xray config and refuse existing occupied ports or foreign
  containers. Preserve pending keys on retry after a network interruption.
- Add a managed-peer forwarding mode with server/transport selection and a
  dedicated local container per forwarding rule. The existing gateway, panel,
  Dokodemo listener and automatic Direct/Dokodemo selection are not restarted or
  repurposed by peer-forward creation/removal.
- Apply the established MSS workaround to managed listeners. Allow authenticated
  private-service forwarding explicitly in new Xray Freedom configurations while
  retaining metadata-network blocks. Support the official non-root Xray image with
  group-readable mounted configs; SSH credentials remain private root-only files.
- Include peer credentials/config in private backups; local uninstall removes only
  containers owned by the current installation. Remote removal remains explicit.
- See [PEERS.md](docs/PEERS.md) for secure access, supported platforms and lifecycle.

### Privileged forwarding ports

Forwarding listeners may use ports below 1024. The original host-network Xray
service now runs with only `NET_BIND_SERVICE` and `no-new-privileges`, with all
other capabilities dropped. Managed local/remote listeners use that same limited
configuration only when a privileged port is requested. This avoids the official
non-root image restarting continuously when a user adds a port such as 80.
