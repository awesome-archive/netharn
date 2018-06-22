# --------------------------------------------------------
# Fast R-CNN
# Copyright (c) 2015 Microsoft
# Licensed under The MIT License [see LICENSE for details]
# Written by Ross Girshick
# --------------------------------------------------------
"""
NUMPY_INCLUDE=$(python -c "import numpy as np; print(np.get_include())")
CPATH=$CPATH:$NUMPY_INCLUDE cythonize -a -i ~/code/clab/clab/models/yolo2/utils/nms/cpu_nms.pyx
"""
from __future__ import absolute_import

import numpy as np
cimport numpy as np

cdef inline np.float32_t max(np.float32_t a, np.float32_t b):
    return a if a >= b else b

cdef inline np.float32_t min(np.float32_t a, np.float32_t b):
    return a if a <= b else b

def cpu_nms(np.ndarray[np.float32_t, ndim=2] dets, np.float thresh, np.float bias=0.0):
    cdef np.ndarray[np.float32_t, ndim=1] x1 = dets[:, 0]
    cdef np.ndarray[np.float32_t, ndim=1] y1 = dets[:, 1]
    cdef np.ndarray[np.float32_t, ndim=1] x2 = dets[:, 2]
    cdef np.ndarray[np.float32_t, ndim=1] y2 = dets[:, 3]
    cdef np.ndarray[np.float32_t, ndim=1] scores = dets[:, 4]

    cdef np.ndarray[np.float32_t, ndim=1] areas = (x2 - x1 + bias) * (y2 - y1 + bias)
    cdef np.ndarray[np.int_t, ndim=1] order = scores.argsort()[::-1]

    cdef int ndets = dets.shape[0]
    cdef np.ndarray[np.int_t, ndim=1] suppressed = \
            np.zeros((ndets), dtype=np.int)

    # nominal indices
    cdef int _i, _j
    # sorted indices
    cdef int i, j
    # temp variables for box i's (the box currently under consideration)
    cdef np.float32_t ix1, iy1, ix2, iy2, iarea
    # variables for computing overlap with box j (lower scoring box)
    cdef np.float32_t xx1, yy1, xx2, yy2
    cdef np.float32_t w, h
    cdef np.float32_t inter, ovr

    keep = []
    for _i in range(ndets):
        # Look at detection in order of descinding score
        i = order[_i]

        # If this detection was not supressed, we will keep it and then supress
        # anything it conflicts with
        if suppressed[i] == 0:
            keep.append(i)
            ix1 = x1[i]
            iy1 = y1[i]
            ix2 = x2[i]
            iy2 = y2[i]
            iarea = areas[i]

            # Look at the other unsupressed detections
            for _j in range(_i + 1, ndets):
                j = order[_j]
                if suppressed[j] == 0:
                    xx1 = max(ix1, x1[j])
                    yy1 = max(iy1, y1[j])
                    xx2 = min(ix2, x2[j])
                    yy2 = min(iy2, y2[j])
                    w = max(0.0, xx2 - xx1 + bias)
                    h = max(0.0, yy2 - yy1 + bias)
                    # Supress any other detection that overlaps with the i-th
                    # detection, which we just kept.
                    inter = w * h
                    ovr = inter / (iarea + areas[j] - inter)
                    if ovr >= thresh:
                        suppressed[j] = 1

    return keep
