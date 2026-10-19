"""File conversion for /convert: format table, detection and local converters.

``formats`` is the single source of truth (matrix, caps); ``detect`` identifies
a file; ``local.convert_bytes`` runs a local conversion; ``media`` builds and
runs the ffmpeg command lines. Heavy libraries are imported lazily, inside the
function that needs them, so importing this package costs almost nothing.
"""
