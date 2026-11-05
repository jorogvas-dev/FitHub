#!/bin/sh
set -eu
cert=/etc/nginx/certs/localhost.crt
key=/etc/nginx/certs/localhost.key
if [ -e "$cert" ] || [ -e "$key" ]; then
    # Never replace existing TLS material automatically, including partial pairs.
    test -s "$cert" && test -s "$key" || {
        echo 'Incomplete TLS certificate/key pair in tlsdata; restore the missing file.' >&2
        exit 1
    }
    openssl x509 -in "$cert" -noout -checkend 0 || {
        echo 'Local TLS certificate expired. Renew it explicitly; see docs/deployment.md.' >&2
        exit 1
    }
else
    umask 077
    openssl req -x509 -nodes -newkey rsa:3072 -sha256 -days 365 \
        -keyout "$key" -out "$cert" -subj '/CN=localhost' \
        -addext 'subjectAltName=DNS:localhost,IP:127.0.0.1,IP:::1'
    chmod 600 "$key"
    chmod 644 "$cert"
fi
