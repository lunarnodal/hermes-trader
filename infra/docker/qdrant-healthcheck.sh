#!/usr/bin/env bash
# Qdrant API healthcheck — image has no curl/wget, so use bash /dev/tcp.
exec 3<>/dev/tcp/127.0.0.1/6333 || exit 1
printf "GET /healthz HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n" >&3
grep -q "200 OK" <&3 || exit 1
