# distortion-waves (vendored)

Source of the `distortion-waves` React package by Jakub Antalik (MIT, see `LICENSE`),
copied from https://github.com/Jakubantalik/Libraries.dev/tree/371b324a253b8ee71b9ce1bc8a1ecc5a59e1a16a/packages/distortion-waves/src.
Docs: https://libraries.dev/waves

Vendored because the package is not on npm yet. Once it is, `pnpm add distortion-waves`,
point the imports at it and delete this folder.

## Local patch

`capture.ts` writes `border-*-width: 0px` when the side has a border style. Without it,
Tailwind's reset (`border: 0 solid`) made every element draw a thin box in the wave's
picture. Search for "SupoClip patch".
