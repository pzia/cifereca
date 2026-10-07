# Third-party references

## epubkit

Project: `b1rdmania/epubkit`
License: MIT
Role: public design/reference for Xteink X3/X4 image parameters and EPUB optimization behavior.

This package does **not** vendor epubkit's FastAPI application or full processing pipeline. The Cifereca engine is a separate, minimal implementation focused on image-only transformation so that XHTML/CSS/metadata remain untouched.

## Pillow

Project: Python Pillow
Role: raster image decoding, resize, grayscale, quantization, dithering and encoding.
License: HPND-style Pillow license. A prebuilt Pillow wheel is redistributed in `wheels/` for the qualified CPython 3.13 Linux x86_64 offline target; Core performs installation into its venv.
