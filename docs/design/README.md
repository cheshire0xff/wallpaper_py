# Design mockups

The look the windows are built from (approved by the user; the rules
below, the details in `app/ui/theme.py`). Dark mode is the one that
matters; light mode only has to stay usable.

## The look

- **Follow the system**: the system's colours, light / dark mode and
  accent, read from the `QPalette`, with our own controls: drop-downs,
  segmented choices, switches, filter chips, buttons and sliders are
  painted in code (`controls.dc.html`; not in it: the filter chips, and
  the switch's NVIDIA-style knob, bigger than its track), so they look
  the same on Windows and KDE. The
  style draws only what is left (text fields, scroll bars, dialogs):
  Windows 11 style on Windows, Fusion on Linux (PySide6's own Qt can't
  load the system's Breeze). No stylesheets and no hard-coded colours,
  except the panel behind images in light mode (wallpapers look washed out
  on white), the status dot's green / red (the palette has no such
  roles), and the badges on the Library's tiles (they sit on the image:
  white outline icons and text on dark discs). The page mockups stay the
  reference for layout.
- **One window with a sidebar**: *Home*, *Library*, *Settings*, each a
  Lucide icon and a label; the selected page has an accent bar on its left
  edge, a faint accent fill and a bold label; the app's status ("Rotation
  active" / paused / error) at the bottom. The title bar is the OS's own.
- **Dark tones**: the sidebar is the page's colour, set apart only by a
  thin lighter line; the page is a shade darker than Qt's default dark
  window; panels behind images are a little lighter than the page, not a
  dark well.
- **Icons**: a small bundled set of Lucide SVGs (ISC licence, kept with
  them), tinted from the palette. Not `QIcon.fromTheme`: Windows has no
  theme icons, and mixing sources looks inconsistent.
- **Text**: at least 10.5 pt (Windows 11's body text is 14 px; Qt's
  default there is 9 pt), so a larger system font is kept.
- **The accent means "what you point at"**: hovering a playlist row
  outlines its monitors; a toggle that is on is the accent icon on a faint
  accent fill. Tooltips and accessible names say what a click does
  ("Remove from favourites", "Resume").
- **Settings as rows, no boxes**: each setting is a row, its name and a
  muted one-line description on the left, its control on the right, all
  controls in one column; fields are filled, without a border; sections
  have a heading and a thin divider. Secondary actions are accent text
  buttons; Save / Revert and the status line sit in a footer bar; sub-tabs
  have an accent underline.
- **Library** follows the same page pattern: tabs, a toolbar (filter,
  sort, *Hide removed* / *Only <playlist>*), the grid on the panel tone,
  a slim fast-scroll bar painted in code.

## Files

- `home.png`: the Home page (monitors panel, playlists as rows).
- `settings.png`: the Settings page (settings as rows, no boxes).
- `library.png`: the Library page (Phase 5 direction).
- `settings-monitors.dc.html`: the monitor-first Settings page (Phase 4b;
  approved, replaces `settings.png`). Interactive: click a monitor, change
  its playlist, mode and background, edit, save and revert playlists.
- `settings-monitors-twins.dc.html`: two monitors of one model ("1 of 2"),
  with the monitor list open (switched-off and not-connected ones last).
- `mockup.dc.html`: the mockup's source, for exact colours, sizes and
  spacing. It is not a runnable page: `{{...}}` are template holes, filled
  in by the script at the bottom (the `dark` theme object holds the
  colours). Pages are switched by `page` / `flow`: Home is
  `page="layout"` with `flow`, Settings `page="settings"` with `flow`;
  light mode's panel tone by `panel` (`silver` is the one built).
- `controls.dc.html`: the controls painted in code (Phase 5, step 2;
  approved): drop-down (closed and open), segmented choice, switch,
  buttons, slider, each at rest, hovered, focused, pressed and disabled,
  in Settings and in the Library's toolbar. Same kind of source as
  `mockup.dc.html`; `dark` and `accent` (the system's, made readable as
  `theme.readable_accent` does) are its inputs. The look it aims for came
  from the NVIDIA app; that screenshot stays out of the repo.

Where a screenshot and the source differ, the screenshot wins. The images
in the mockup are colourful stand-ins, not real wallpapers. Colours are
the Windows 11 dark palette; the app takes them from the system palette
(QPalette), so the values here are a reference, not constants to copy.
