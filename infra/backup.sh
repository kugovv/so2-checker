#!/bin/sh
set -eu
umask 077
dir=/var/lib/so2-check/backups
stamp=$(date -u +%Y%m%dT%H%M%SZ)
tmp="$dir/so2-check-$stamp.dump.partial"
pg_dump --format=custom --no-owner --no-acl --dbname=so2check --username=so2check --file="$tmp"
pg_restore --list "$tmp" >/dev/null
mv "$tmp" "$dir/so2-check-$stamp.dump"
# Only successful app-specific backups older than fourteen days are rotated.
find "$dir" -maxdepth 1 -type f -name 'so2-check-*.dump' -mtime +14 -delete
printf 'Backup complete: %s\n' "$dir/so2-check-$stamp.dump"
