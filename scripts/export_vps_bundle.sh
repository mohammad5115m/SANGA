#!/usr/bin/env bash
# Run after the production container smoke test, on a clean Linux CI checkout.
# Export ready images; never read or include a VPS environment file or volume.
set -euo pipefail

output_dir="${1:?Usage: export_vps_bundle.sh OUTPUT_DIRECTORY}"
mkdir -p "$output_dir"
source_sha="$(git rev-parse HEAD)"
app_image="sanga:vps-${source_sha:0:12}"
docker tag sanga-ci "$app_image"

images=("$app_image" postgres:16-alpine redis:7-alpine alpine:3.22 caddy:2-alpine)
for image in "${images[@]:1}"; do
  docker pull --platform linux/amd64 "$image"
done
for image in "${images[@]}"; do
  test "$(docker image inspect --format '{{.Os}}/{{.Architecture}}' "$image")" = linux/amd64
done
docker run --rm --network none "$app_image" sh -c 'test ! -e /app/.env'

cat > "$output_dir/docker-compose.offline.yml" <<EOF
# Use after docker-compose.prod.yml and the existing docker-compose.vps.yml.
# Keep the existing project directory, volumes, environment and Caddyfile.
services:
  web:
    image: $app_image
    pull_policy: never
  migrate:
    image: $app_image
    pull_policy: never
  db:
    image: postgres:16-alpine
    pull_policy: never
  redis:
    image: redis:7-alpine
    pull_policy: never
  media-init:
    image: alpine:3.22
    pull_policy: never
  edge:
    image: caddy:2-alpine
    pull_policy: never
EOF
docker compose -f "$output_dir/docker-compose.offline.yml" config --quiet

{
  printf 'Repository: mohammad5115m/SANGA\nSource commit: %s\nPlatform: linux/amd64\n' "$source_sha"
  printf 'Application image: %s\n\n' "$app_image"
  docker image inspect --format '{{join .RepoTags ","}} {{.Id}} {{.Os}}/{{.Architecture}}' "${images[@]}"
} > "$output_dir/RELEASE.txt"

docker image save "${images[@]}" | gzip -1 > "$output_dir/sanga-images.tar.gz"
gzip -t "$output_dir/sanga-images.tar.gz"
cp docs/vps-offline-deployment-fa.md "$output_dir/README-fa.md"
(
  cd "$output_dir"
  sha256sum sanga-images.tar.gz docker-compose.offline.yml RELEASE.txt README-fa.md > SHA256SUMS
)
