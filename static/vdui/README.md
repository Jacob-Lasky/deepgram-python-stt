# Deepgram brand UI kit assets

Copied from [`deepgram-devs/dg-brand-ui`](https://github.com/deepgram-devs/dg-brand-ui) **v0.6.0** (`assets/`). This app has no build step, so the files are committed here and served from `/static/vdui/` by the app's existing `/static` mount.

| File | What it is |
| --- | --- |
| `brand.css` | The kit's theme as plain CSS: tokens (including its dark theme, which the page turns on with `data-theme="dark"`), fonts, and the `label` utility |
| `logos/deepgram.svg`, `logos/deepgram-favicon.svg` | The Deepgram logo and favicon |

The page uses the kit's paper surface (in its dark theme) only, so the textures (`textures/`, for `stage-glow` and `landing-texture`) and the voice ring aren't copied. Copy them from the kit if the page starts using those.

Don't edit these files. To upgrade, copy the same files from the kit's `assets/` folder at the new tag, then update the version above.
