#!/usr/bin/env python

import os
import sys

from cairosvg import svg2png

for infile in sys.argv[1:]:
    file, ext = os.path.splitext(infile)
    with open(infile) as f:
        try:
            svg2png(
                file_obj=f,
                write_to=file + ".new.png",
                output_height=512,
                output_width=512,
                dpi=300,
                scale=2,
            )
        except Exception as e:
            print(infile, e)
