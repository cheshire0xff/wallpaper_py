# Design

The look the windows are built from (approved by the user; the rules
below, the details in `app/ui/theme.py`). The app itself is the reference
for layout. Dark mode is the one that matters; light mode only has to stay
usable.

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
  white outline icons and text on dark discs).
- **One window with a sidebar**: *Home*, *Library*, *Settings*, each a
  Lucide icon and a label; the selected page has an accent bar on its left
  edge, a faint accent fill and a bold label; the app's status ("Rotation
  active" / paused / error) at the bottom. The title bar is the OS's own,
  painted in the page's colour where the system lets an app (Windows 11),
  with no line under it: the page runs up into it, as in Windows' Photos.
- **A header band on each page**, as in the NVIDIA app: one solid tone a
  little lighter than the page, 64 px, the page's title on the left and
  its own controls on the right (Library: the count and a flat playlist
  drop-down); the tabs below it.
- **Dark tones**: near black, as in Windows' Photos (the palette's window
  a third as light); the sidebar is the page's colour, set apart only by a
  thin lighter line; panels behind images are a little lighter than the
  page, not a dark well.
- **Icons**: a small bundled set of Lucide SVGs (ISC licence, kept with
  them), tinted from the palette. Not `QIcon.fromTheme`: Windows has no
  theme icons, and mixing sources looks inconsistent.
- **Text**: Segoe UI Variable on Windows (Windows 11's own; Segoe UI
  has no Medium), the system's font elsewhere. Content sized as in the
  NVIDIA app: a setting's name as large as the tabs, in Medium; muted
  text at body size; a control's text a little larger (`CONTROL_TEXT`,
  also Home's file names); fields 40 px tall. Body text at least 10.5 pt
  (Windows 11's is 14 px; Qt's default there is 9 pt), so a larger system
  font is kept.
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
  a slim fast-scroll bar painted in code. A tile opens enlarged over the
  whole page: the image on the panel tone under a bar on the page tone,
  and the info card on the page tone over the image's bottom left.

## Files

- `controls.dc.html`: the controls painted in code (Phase 5, step 2;
  approved): drop-down (closed and open), segmented choice, switch,
  buttons, slider, each at rest, hovered, focused, pressed and disabled,
  in Settings and in the Library's toolbar. Not a runnable page: `{{...}}`
  are template holes, filled in by the script at the bottom; `dark` and
  `accent` (the system's, made readable as `theme.readable_accent` does)
  are its inputs. The look it aims for came from the NVIDIA app; that
  screenshot stays out of the repo.

The page mockups (Home, Settings, Library; screenshots and their source)
were the target before the pages were built; the app has since moved past
them, so they went. They are in git: `git show 4ea9572 -- docs/design/`.
The colours here are the Windows 11 dark palette; the app takes them from
the system palette (QPalette), so they are a reference, not constants to
copy.
