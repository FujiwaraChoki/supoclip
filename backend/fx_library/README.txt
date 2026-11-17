Dynamic edits drop-in library (FX_LIBRARY_DIR)
============================================

Put your own sound effects and music beds here to replace the built-in ones (CC0 sound
effects in src/fx_assets/sfx, CC0 music beds in src/fx_assets/music), the same way
backend/fonts/ adds fonts. The folder ships empty.

  sfx/<sound_id>[-<name>].{wav,mp3,m4a,ogg}
      sound_id is one of: whoosh impact pop riser record_scratch boom cash_register
      ticker slap stamp glitch tape_stop
      e.g. sfx/whoosh-swish.wav, sfx/boom.m4a

  music/<mood>-<name>.{m4a,mp3,wav}
      mood is one of: hype chill dramatic funny inspiring dark aggressive
      e.g. music/chill-lofi-rain.m4a
      (a mood's drop-ins replace all of its built-in beds)

How they are used
- Drop-ins never change which effects a clip gets. The plan only ever names a sound or
  a mood; the file is picked when the audio is mixed. When a sound or mood has drop-ins,
  they replace the built-in files for it (one is picked per clip).
- Each file is measured once per process and gain-normalised, so levels match the
  built-in set. Keep sound effects short (under 2.5 s) and music beds loopable.
- Risers are picked by length: a cue that would play a 1.5 s built-in riser gets one of
  your risers closest to 1.5 s, a 2.5 s one the closest to 2.5 s. Provide both lengths
  (e.g. sfx/riser-1s5-mine.wav, sfx/riser-2s5-mine.wav) and end each on its peak.
- Every machine that renders (the API host's worker and every remote render worker)
  must mount the same folder, or the studio preview and the server render will play
  different files. Renders log a library fingerprint so a mismatch is visible.

Only add files you have the rights to use in the videos you publish.
