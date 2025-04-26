#!/usr/bin/env python

import os
import sys

from PIL import Image, ImageFilter

for infile in sys.argv[1:]:
    file, ext = os.path.splitext(infile)
    with Image.open(infile) as im:
        try:
            im = im.filter(ImageFilter.EDGE_ENHANCE)
            im = im.filter(ImageFilter.SHARPEN)

            im.save(file + "f" + ext, "PNG")
        except Exception as e:
            print(infile, e)
