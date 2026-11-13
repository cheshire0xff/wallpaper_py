# Design mockups

The look the windows are built from (approved by the user; see "Look" and
"One window" in [plan.md](../plan.md)). Dark mode is the one that matters;
light mode only has to stay usable.

- `home.png`: the Home page (monitors panel, playlists as rows).
- `settings.png`: the Settings page (settings as rows, no boxes).
- `library.png`: the Library page (Phase 5 direction).
- `mockup.dc.html`: the mockup's source, for exact colours, sizes and
  spacing. It is not a runnable page: `{{...}}` are template holes, filled
  in by the script at the bottom (the `dark` theme object holds the
  colours). Pages are switched by `page` / `flow`: Home is
  `page="layout"` with `flow`, Settings `page="settings"` with `flow`.

Where a screenshot and the source differ, the screenshot wins. The images
in the mockup are colourful stand-ins, not real wallpapers. Colours are
the Windows 11 dark palette; the app takes them from the system palette
(QPalette), so the values here are a reference, not constants to copy.
