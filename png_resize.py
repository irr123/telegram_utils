#!/usr/bin/env python

import os
import sys

from PIL import Image

size = 512, 512

for infile in sys.argv[1:]:
    file, ext = os.path.splitext(infile)
    with Image.open(infile) as im:
        im.thumbnail(size)
        im.save(file + ".new.png", "PNG")
