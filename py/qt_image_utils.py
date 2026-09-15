"""Qt image helpers shared by adjust.py and map.py (avoid map importing full adjust)."""

import numpy as np
from qtpy.QtGui import QImage


def numpy_array_to_qimage(array):
    """Convert a numpy array to a QImage."""
    if np.ndim(array) == 3:
        h, w, ch = array.shape
        if array.flags["C_CONTIGUOUS"]:
            array = array.copy(order="C")
        if ch == 3:
            format = QImage.Format.Format_RGB888
        elif ch == 4:
            format = QImage.Format.Format_ARGB32
        else:
            raise ValueError("Unsupported channel number: {}".format(ch))
    elif np.ndim(array) == 2:
        h, w = array.shape
        if not array.flags["C_CONTIGUOUS"]:
            array = np.ascontiguousarray(array)
        format = QImage.Format.Format_Grayscale8
    else:
        raise ValueError("Unsupported numpy array shape: {}".format(array.shape))

    qimage = QImage(array.data, w, h, array.strides[0], format)
    qimage.ndarray = array
    return qimage
