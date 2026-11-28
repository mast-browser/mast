#!/usr/bin/env bash
# A build nobody watches (./mast unattended): build, screenshots, then snapshot this builder
# and delete it, all from here, so the laptop can be off. ./mast collect picks it up later.
#
#   bash remote/unattended.sh <server name> <deadline hours> [screenshot areas]
#
# The Hetzner token arrives in /root/.mast-hcloud-token. It is read into memory and the file
# deleted before anything else, so it is never in a snapshot, a log or a later boot. Past the
# deadline the job is stopped and the builder is still snapshotted and deleted.
set -u
NAME=$1
DEADLINE=$(($(date +%s) + ${2:-8} * 3600))
AREAS=${3:-basics}  # remote/shots.py's areas, comma-separated
M=/root/mast
say() { echo "[$(date '+%F %T')] $*"; }

HCLOUD_TOKEN=$(cat /root/.mast-hcloud-token 2>/dev/null) || true
shred -u /root/.mast-hcloud-token 2>/dev/null || rm -f /root/.mast-hcloud-token
[ -n "$HCLOUD_TOKEN" ] || { say "no token: building, but the builder can't delete itself"; }
export HCLOUD_TOKEN

# Nothing else may stop this machine: a stopped Hetzner server still bills, and this script
# would never get to delete it.
systemctl disable --now mast-idle-stop.timer >/dev/null 2>&1 || true

if ! command -v hcloud >/dev/null; then
	say "installing hcloud"
	curl -fsSL --retry 5 https://github.com/hetznercloud/cli/releases/latest/download/hcloud-linux-amd64.tar.gz |
		tar -xz -C /usr/local/bin hcloud
fi

# Build, then screenshots, in the background, so the deadline can stop them.
(
	cd "$M"
	rm -f build.status shots.status
	say "build"
	bash remote/build.sh > build.log 2>&1
	echo $? > build.status
	if [ "$(cat build.status)" = 0 ]; then
		say "screenshots: $AREAS"
		MAST_SHOTS="$AREAS" bash remote/shots.sh > shots.log 2>&1
		echo $? > shots.status
	fi
) &
WORK=$!
while kill -0 "$WORK" 2>/dev/null; do
	if [ "$(date +%s)" -ge "$DEADLINE" ]; then
		say "deadline reached: stopping the build"
		echo deadline > "$M/deadline.status"
		pkill -TERM -P "$WORK"; pkill -f 'siso|ninja|remote/(build|shots)\.sh|out/Mast/chrome' ; sleep 20
		kill -9 "$WORK" 2>/dev/null
		break
	fi
	sleep 60
done
wait "$WORK" 2>/dev/null
say "build: $(cat "$M/build.status" 2>/dev/null || echo none), shots: $(cat "$M/shots.status" 2>/dev/null || echo none)"

# Snapshot (keeps the checkout and build cache for the next build), then delete. Retries
# until the delete succeeds: a builder left running is the one outcome that costs money.
sync
[ -n "$HCLOUD_TOKEN" ] || { say "no token: shutting down instead (still billed until deleted)"; shutdown -h now; exit 1; }
for i in $(seq 10); do
	say "snapshot (attempt $i)"
	hcloud server create-image "$NAME" --type snapshot --description "mast $(date +%F) unattended" \
		--label mast=builder >/dev/null && { say "snapshot saved"; break; }
	sleep 60
done
while :; do
	say "deleting this builder"
	hcloud server delete "$NAME" && break
	sleep 300
done
