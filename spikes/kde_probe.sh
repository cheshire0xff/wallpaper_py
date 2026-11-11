#!/usr/bin/env bash
# Phase 0 spike for KDE Plasma (Arch / Kubuntu). READ-ONLY: changes nothing.
#
# Answers:
#   1. Which wallpaper methods does plasmashell expose over D-Bus (setWallpaper?)
#   2. How Plasma's screen numbers / desktops map to physical outputs
#   3. Per-activity wallpapers: how many desktops (containments) exist
#   4. With --snapshot NAME: save KScreen + Plasma state, so you can compare
#      OLED on vs off:   ./kde_probe.sh --snapshot oled-on
#                         (switch OLED off, wait ~30 s)
#                         ./kde_probe.sh --snapshot oled-off
#
# Output goes to stdout and spikes/kde_probe-<name>.log

set -u
cd "$(dirname "$0")"
NAME="${2:-full}"
LOG="kde_probe-${NAME}.log"
exec > >(tee "$LOG") 2>&1

section() { printf '\n===== %s\n' "$1"; }

section "environment"
echo "date: $(date -Is)"
echo "session: ${XDG_SESSION_TYPE:-?}  desktop: ${XDG_CURRENT_DESKTOP:-?}"
plasmashell --version 2>/dev/null || echo "plasmashell --version failed"
command -v kscreen-doctor >/dev/null || echo "kscreen-doctor missing (package: libkscreen / kscreen)"

JS='
var out = [];
var acts = activities();
out.push("activities: " + JSON.stringify(acts) + "  current: " + currentActivity());
for (var a = 0; a < acts.length; a++) {
  var ds = desktopsForActivity(acts[a]);
  for (var i = 0; i < ds.length; i++) {
    var d = ds[i];
    var g = d.screen >= 0 ? screenGeometry(d.screen) : null;
    d.currentConfigGroup = ["Wallpaper", d.wallpaperPlugin, "General"];
    out.push(JSON.stringify({
      activity: acts[a], id: d.id, screen: d.screen,
      geometry: g ? [g.x, g.y, g.width, g.height] : null,
      plugin: d.wallpaperPlugin,
      image: d.readConfig("Image"), fillMode: d.readConfig("FillMode")
    }));
  }
}
print(out.join("\n"));
'

call_plasma() {
  if command -v qdbus6 >/dev/null; then
    qdbus6 org.kde.plasmashell /PlasmaShell "$@"
  else
    busctl --user call org.kde.plasmashell /PlasmaShell org.kde.PlasmaShell "$@"
  fi
}

if [ "$NAME" = "full" ]; then
  section "plasmashell D-Bus interface (look for setWallpaper / wallpaper)"
  busctl --user introspect org.kde.plasmashell /PlasmaShell org.kde.PlasmaShell 2>&1
fi

section "Plasma desktops per activity (evaluateScript, read-only)"
if command -v qdbus6 >/dev/null; then
  call_plasma org.kde.PlasmaShell.evaluateScript "$JS"
else
  call_plasma evaluateScript s "$JS"
fi

section "KScreen outputs (connector, EDID ids, enabled/connected, geometry, priority)"
kscreen-doctor -j 2>/dev/null | python3 -c '
import json, sys
d = json.load(sys.stdin)
for o in d.get("outputs", []):
    print(json.dumps({k: o.get(k) for k in (
        "id", "name", "connected", "enabled", "priority", "pos", "size", "scale",
        "rotation") } | {"edid": {k: (o.get("edid") or {}).get(k) for k in (
        "vendor", "name", "serial", "hash")} if o.get("edid") else None},
        ensure_ascii=False))
' || kscreen-doctor -o

section "Qt view (if the repo venv exists)"
if [ -x ../.venv/bin/python ]; then
  ../.venv/bin/python monitor_probe.py | sed 's/^/  /'
else
  echo "no ../.venv/bin/python; skip"
fi

echo
echo "saved to spikes/$LOG"
