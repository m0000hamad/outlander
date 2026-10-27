# Changes

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
