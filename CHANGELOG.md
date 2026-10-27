# Changes

## Console 1.2 — managed peer port tunnels

- Add bilingual Peer servers page with root SSH password/private-key registration,
  connection test, pinned host keys, background setup status, transport removal,
  and credential deletion. Block credential submissions over public plaintext HTTP.
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
