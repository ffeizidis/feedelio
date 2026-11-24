#!/bin/sh
# One-time move of the library into the pinned volume.
#
# Compose named the data volume <project>_feedelio-data until compose.yaml pinned it to
# feedelio-data, the name the plain docker run instructions use. Without this an upgraded
# Compose deployment would start on an empty volume while the old library sits unused.
# Copies only when the old volume exists and the new one does not; never deletes the old.
#
# Usage: migrate-volume.sh OLD NEW IMAGE   (stop the container that uses OLD first)
set -eu
old=$1 new=$2 image=$3
docker volume inspect "$old" >/dev/null 2>&1 || exit 0
docker volume inspect "$new" >/dev/null 2>&1 && exit 0
echo "Copying $old into $new; $old is left in place."
docker volume create "$new" >/dev/null
docker run --rm --user 0 --entrypoint cp -v "$old":/from:ro -v "$new":/to "$image" -a /from/. /to/
