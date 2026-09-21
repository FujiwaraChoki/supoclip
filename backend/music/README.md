# Background music library

Drop your own `.mp3`, `.wav`, `.m4a`, or `.ogg` background-music beds in this
directory and they become selectable as a ranking template's
`background_music` (see `backend/templates/ranking/<name>/config.json`).
The Ranking tool's render pipeline loops the track to the compilation's
length and automatically ducks it under each clip's own audio via an
ffmpeg sidechain compressor — no per-clip timing/volume work needed.

No files are bundled here by default — background music is typically
licensed per-track, so choose your own rather than relying on one shipped
in this repo (same rationale as `backend/sfx/README.md`).
