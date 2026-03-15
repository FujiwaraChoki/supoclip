# Fonts

This directory contains subtitle fonts that SupoClip can expose in the UI and use during clip rendering.

## How it works

- The backend scans this directory for supported font files.
- The frontend fetches the list through the fonts API.
- Selected fonts are applied to subtitles during rendering and editing flows.

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

