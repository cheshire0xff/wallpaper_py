## Normal prio
1. Selectable images in grid where you can decide what to do with them like to fav them, remove then or undo etc
   (docs/plan.md, Phase 7, "Library, later").
1. Separate the library viewer to make something like gwenview out of it?
1. Make fonts bold and bigger in some places like nvidia app.
1. Home: a click on a playlist's current image opens it enlarged (the Library's enlarged view).
1. Start at login in the window's Settings (today only in the tray menu).

## Nice to have

1. Sound notification when the wallpaper changes (I have this on Linux already), so I notice new images.
   On / off per playlist (in the settings panel), off by default.
1. More CLI verbosity, e.g. `--debug` (debug level): each step of reaching the app
   (socket attempts, taking / waiting for / releasing the lock, timings), on top of
   `--verbose`'s summary.
1. Screenshots in the README (the settings window with monitor tiles, the library, the tray menu):
   they sell the app better than any description. Light and dark mode; keep them current when the UI changes.
1. Virtual backend (made-up monitors, nothing set on the real desktop) for end-to-end
   tests and a demo mode, and page renders uploaded by CI (docs/plan.md, Phase 7).

1. Hide detached / stale monitor entries from the Home overview, so only
   currently connected displays appear there (today Home draws them to the
   right of the arrangement, dimmed and marked "off").
   Possible approaches:
   - Add a UI action to forget a detached monitor and persist the exclusion
     in the settings, so it stays hidden.
   - Investigate whether Windows exposes a last-attached time; if so, consider
     hiding entries that have been detached for more than a week.
1. Auto updates?
