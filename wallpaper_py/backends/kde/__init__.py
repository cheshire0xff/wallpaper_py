"""
KDE Plasma: wallpapers through plasmashell's scripting (D-Bus
`evaluateScript`), monitors from KScreen (`kscreen-doctor`), names from
EDID in sysfs, the lock state from the screen saver's D-Bus interface.

Everything goes through two command-line tools (`kscreen-doctor`,
`busctl`) instead of a D-Bus library: no Qt below `app/`, no extra
dependency, and both ship with Plasma (KScreen) and systemd.
"""
