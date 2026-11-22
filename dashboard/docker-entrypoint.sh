#!/bin/sh
# nginx needs the DNS server as an address, but the address differs per cluster
# and per CNI, so it cannot be baked into the image. Read the nameserver the pod
# was given and render the config with it before starting nginx.
set -eu

resolver="$(awk '/^nameserver/ { print $2; exit }' /etc/resolv.conf)"
if [ -z "${resolver}" ]; then
    echo "no nameserver in /etc/resolv.conf; refusing to start without cluster DNS" >&2
    exit 1
fi

sed "s/__KUBE_DNS__/${resolver}/" /etc/nginx/nginx.conf > /tmp/nginx.conf

exec nginx -c /tmp/nginx.conf -g 'daemon off;'
