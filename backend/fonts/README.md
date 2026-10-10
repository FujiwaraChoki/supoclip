# Fonts

This directory contains subtitle fonts that SupoClip can expose in the UI and use during clip rendering.

## How it works

- The backend scans this directory for supported font files.
- The frontend fetches the list through the fonts API.
- Selected fonts are applied to subtitles during rendering and editing flows.
- Each font's info popover shows the languages covered by its actual glyphs,
  including uploaded fonts. The API returns these language tags in
  `supported_languages`; `null` means the coverage check was unavailable.
- Docker images include Noto core, CJK and emoji fallback fonts. Rebuild both the
  backend and worker images to install them on an existing deployment. For local
  installs, install Fontconfig and the corresponding Noto packages with your OS
  package manager. A font that lacks a caption's characters uses these fallbacks,
  which can change the appearance; preview non-English captions before export.

## Supported formats

- `.ttf`
- `.otf`

## Adding a font

1. Copy the font file into this directory.
2. Restart the stack if the new file does not appear immediately.
3. Confirm the font is available through the app or `GET /fonts`.

## Notes

- Keep file names stable if they are already referenced by saved tasks or user defaults.
- Large font collections can make the picker harder to use, so prefer a curated set.
- Source and licensing notes for bundled fonts should go in [`SOURCES.md`](./SOURCES.md).
