#!/usr/bin/env bash
# Allowlist for the Trading Pipeline MCP port (R2-6 interim for D-3).
#
#   sudo ops/mcp_firewall.sh install   # apply now + persist across reboot
#   sudo ops/mcp_firewall.sh status
#   sudo ops/mcp_firewall.sh remove
#
# Uses its own nftables table (inet trading_mcp), so it never edits or flushes
# any other ruleset (ufw, docker, etc.). Only TCP to MCP_PORT is affected:
# loopback and ALLOW_FROM are accepted, every other source is dropped.
set -euo pipefail

MCP_PORT="${MCP_PORT:-8101}"
ALLOW_FROM="${ALLOW_FROM:-172.29.10.220}"      # Hermes host (seen polling /mcp until 10/08)
TABLE="trading_mcp"
UNIT=/etc/systemd/system/trading-mcp-firewall.service
RULES=/etc/nftables.d/trading_mcp.nft

[[ $EUID -eq 0 ]] || { echo "run with sudo"; exit 1; }
command -v nft >/dev/null || { echo "nft not installed (apt install nftables)"; exit 1; }

render() {
  cat <<EOF
table inet ${TABLE} {
  chain input {
    type filter hook input priority -10; policy accept;
    tcp dport ${MCP_PORT} iifname "lo" accept
    tcp dport ${MCP_PORT} ip saddr { ${ALLOW_FROM} } accept
    tcp dport ${MCP_PORT} counter drop
  }
}
EOF
}

case "${1:-}" in
  install)
    mkdir -p "$(dirname "$RULES")"
    { echo "table inet ${TABLE}"; echo "delete table inet ${TABLE}"; render; } > "$RULES"
    nft -f "$RULES"
    cat > "$UNIT" <<EOF
[Unit]
Description=nftables allowlist for Trading Pipeline MCP port ${MCP_PORT}
After=network-pre.target
Before=trading-pipeline-mcp.service

[Service]
Type=oneshot
ExecStart=/usr/sbin/nft -f ${RULES}
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    systemctl enable trading-mcp-firewall.service >/dev/null
    echo "installed: port ${MCP_PORT} allowed from lo + ${ALLOW_FROM}; all else dropped"
    nft list table inet "$TABLE"
    ;;
  status)
    nft list table inet "$TABLE" 2>/dev/null || echo "table inet ${TABLE} not loaded"
    systemctl is-enabled trading-mcp-firewall.service 2>/dev/null || true
    ;;
  remove)
    nft delete table inet "$TABLE" 2>/dev/null || true
    systemctl disable trading-mcp-firewall.service 2>/dev/null || true
    rm -f "$UNIT" "$RULES"; systemctl daemon-reload
    echo "removed"
    ;;
  *) echo "usage: $0 install|status|remove"; exit 2 ;;
esac
