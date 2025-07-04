#!/usr/bin/env python

import os
import sys

from reportlab.graphics import renderPM
from svglib.svglib import svg2rlg

for infile in sys.argv[1:]:
    file, ext = os.path.splitext(infile)
    try:
        drawing = svg2rlg(infile)
        renderPM.drawToFile(
            drawing,
            file + ".new.png",
            fmt="PNG",
            dpi=300,
            bg=None,
        )
    except Exception as e:
        print(infile, e)
