1. Running wallpaper playlist service.
It can automatically go over or not local directory of images(sorted, shuffled etc).
History has to be preseved across boots. This is needed for example for shuffle to work properly.
Each monitor can have it's own dir and cycle interval, but dosn't have to, mirroring should be an option.
My main monitor is often turned off for OLED refresh so we need a way to handle this case, maybe postpone the cycle or just skip it entirely, worst case is setting the wallpaper on wrong monitor, this we want to prevent.

1. Way to mark wallpaper favourite, has to be persistent I usually just do .txt files with full paths, but maybe yaml would be better so at least we can split path from filename
1. Way to mark wallpaper as to be removed/deleted
again usally I just do .txt but yaml might be better.
(in my case I don't actually wan't to just delete local image, it might be a symlink to upscaled version of original image,
so I need to pass it first through my lib management script to delete it properly)
1. Way to go to next and previous wallpaper, per monitor(if mirrored then just change all mirrored monitors)

1. Investiage if this is still the preffered way to manage monitors and wallpapers on windows.

## Library window

Modelled on my phone app (C:\Users\fanta\Repos\WallpaperSwitcherApp), minus
its manual "set as wallpaper" and lock screen parts: here the playlists set
the wallpapers.

1. Tabs: All images, Favourites, To remove, History, Upcoming. The last
   opened tab is remembered.
1. Every image in every grid can be clicked to enlarge it. The enlarged view
   shows the full path (selectable, copyable), dimensions and file size, and
   has favourite and to-remove buttons that toggle (un-favourite and
   un-remove too).
1. Enlarged view: zoom (wheel, up to ~5x; double-click toggles zoom / fit)
   and drag to pan; click hides / shows the info overlay.
1. Filter by name: case-insensitive part of the filename, with live results
   (small thumbnails) while typing; picking one opens it enlarged.
1. Sort each tab, remembered per tab: name A-Z / Z-A, newest / oldest
   added.
1. "Hide removed" toggle in All images.
1. Image count of the current tab / filter.
1. Grid: columns adapt to the window width; draggable fast-scroll
   scrollbar; jumps back to the top when the sort or filter changes.
   Must stay smooth with 20k images.
1. Current wallpaper per playlist (in the layout window): thumbnail, name,
   size, dimensions, favourite / to-remove toggles, click to enlarge.
1. Reset the shuffle cycle (start over, everything unseen again), with a
   confirmation.
1. Settings / about: data and config paths, version with git hash.
   More, like the phone app's info dialog (nice to have):
   - Data locations, each with *Open folder* / *Copy path*: the config file,
     the state database, favourites.txt and to-remove.txt, the logs (app
     and UI), the render and thumbnail caches (with their size on disk).
   - Library stats per playlist: images in its directory, seen / unseen in
     the current shuffle cycle, history length; favourites and to-remove
     counted as "listed" vs "still exist" (a listed file may be gone).
   - Totals across playlists, and the time the directory was last scanned.

## Nice to have

1. Library grid: fade in from black when a thumbnail loads (like the phone app).
1. Sound notification when the wallpaper changes (I have this on Linux already), so I notice new images.
   On / off per playlist (in the config and the settings panel), off by default.
1. More CLI verbosity, e.g. `--debug` (debug level): each step of reaching the app
   (socket attempts, taking / waiting for / releasing the lock, timings), on top of
   `--verbose`'s summary.
1. Screenshots in the README (the settings window with monitor tiles, the library, the tray menu):
   they sell the app better than any description. Light and dark mode; keep them current when the UI changes.
1. Virtual backend (made-up monitors, nothing set on the real desktop) for end-to-end
   tests and a demo mode, and page renders uploaded by CI (docs/plan.md, Phase 7).
