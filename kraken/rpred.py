# -*- coding: utf-8 -*-
#
# Copyright 2015 Benjamin Kiessling
# 
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# 
# http://www.apache.org/licenses/LICENSE-2.0
# 
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express
# or implied. See the License for the specific language governing
# permissions and limitations under the License.

from __future__ import absolute_import, division, print_function
from __future__ import unicode_literals
from future import standard_library
standard_library.install_aliases()
from builtins import range
from builtins import object

import numpy as np
import bidi.algorithm as bd
from PIL import ImageOps

from kraken.lib import lstm
from kraken.lib.util import pil2array, array2pil
from kraken.lib.lineest import CenterNormalizer
from kraken.lib.models import ClstmSeqRecognizer
from kraken.lib.exceptions import KrakenInputException


class ocr_record(object):
    """
    A record object containing the recognition result of a single line
    """
    def __init__(self, prediction, cuts, confidences):
        self.prediction = prediction
        self.cuts = cuts
        self.confidences = confidences

    def __len__(self):
        return len(self.prediction)

    def __str__(self):
        return self.prediction

    def __iter__(self):
        self.idx = -1
        return self

    def __next__(self):
        if self.idx + 1 < len(self):
            self.idx += 1
            return (self.prediction[self.idx], self.cuts[self.idx],
                    self.confidences[self.idx])
        else:
            raise StopIteration

    def __getitem__(self, key):
        if isinstance(key, slice):
            return [self[i] for i in range(*key.indices(len(self)))]
        elif isinstance(key, int):
            if key < 0:
                key += len(self)
            if key >= len(self):
                raise IndexError('Index (%d) is out of range' % key)
            return (self.prediction[key], self.cuts[key],
                    self.confidences[key])
        else:
            raise TypeError('Invalid argument type')


def bidi_record(record):
    """
    Reorders a record using the Unicode BiDi algorithm. 
    
    Models trained for RTL or mixed scripts still emit classes in LTR order
    requiring reordering for proper display.

    Args:
        record (kraken.rpred.ocr_record)

    Returns:
        kraken.rpred.ocr_record 
    """
    storage = bd.get_empty_storage()
    base_level = bd.get_base_level(record.prediction)
    storage['base_level'] = base_level
    storage['base_dir'] = ('L', 'R')[base_level]

    bd.get_embedding_levels(record.prediction, storage)
    bd.explicit_embed_and_overrides(storage)
    bd.resolve_weak_types(storage)
    bd.resolve_neutral_types(storage, False)
    bd.resolve_implicit_levels(storage, False)
    for i, j in enumerate(record):
        storage['chars'][i]['record'] = j
    bd.reorder_resolved_levels(storage, False)
    bd.apply_mirroring(storage, False)
    prediction = u''
    cuts = []
    confidences = []
    for ch in storage['chars']:
        prediction = prediction + ch['record'][0]
        cuts.append(ch['record'][1])
        confidences.append(ch['record'][2])
    return ocr_record(prediction, cuts, confidences)


def extract_boxes(im, bounds):
    """
    Yields the subimages of image im defined in the list of bounding boxes in
    bounds preserving order.

    Args:
        im (PIL.Image): Input image
        bounds (list): A list of tuples (x1, y1, x2, y2)

    Yields:
        (PIL.Image) the extracted subimage
    """
    for box in bounds:
        if (box < (0, 0, 0, 0) or box[::2] > (im.size[0], im.size[0]) or
           box[1::2] > (im.size[1], im.size[1])):
            raise KrakenInputException('Line outside of image bounds')
        yield im.crop(box), box


def dewarp(normalizer, im):
    """
    Dewarps an image of a line using a kraken.lib.lineest.CenterNormalizer
    instance.

    Args:
        normalizer (kraken.lib.lineest.CenterNormalizer): A line normalizer
                                                          instance
        im (PIL.Image): Image to dewarp

    Returns:
        PIL.Image containing the dewarped image.
    """
    line = pil2array(im)
    temp = np.amax(line)-line
    temp = temp*1.0/np.amax(temp)
    normalizer.measure(temp)
    line = normalizer.normalize(line, cval=np.amax(line))
    return array2pil(line)


def rpred(network, im, bounds, pad=16, line_normalization=True, bidi_reordering=True):
    """
    Uses a RNN to recognize text

    Args:
        network (kraken.lib.lstm.SegRecognizer): A SegRecognizer object
        im (PIL.Image): Image to extract text from
        bounds (iterable): An iterable returning a tuple defining the absolute
                           coordinates (x0, y0, x1, y1) of a text line in the
                           Image.
        pad (int): Extra blank padding to the left and right of text line
        line_normalization (bool): Dewarp line using the line estimator
                                   contained in the network. If no normalizer
                                   is available one using the default
                                   parameters is created. By aware that you may
                                   have to scale lines manually to the target
                                   line height if disabled.
        bidi_reordering (bool): Reorder classes in the ocr_record according to
                                the Unicode bidirectional algorithm for correct
                                display.
    Yields:
        An ocr_record containing the recognized text, absolute character
        positions, and confidence values for each character. 
    """
    if isinstance(network, ClstmSeqRecognizer):
        for out in _rpred_clstm(network, im, bounds, pad, bidi_reordering):
            yield out
        raise StopIteration

    lnorm = getattr(network, 'lnorm', CenterNormalizer())

    for box, coords in extract_boxes(im, bounds):
        # check if boxes are non-zero in any dimension
        if sum(coords[::2]) == False or coords[3] - coords[1] == False:
            yield ocr_record('', [], [])
            continue
        if not isinstance(network, ClstmSeqRecognizer):
            raw_line = pil2array(box)
            # check if line is non-zero
            if np.amax(raw_line) == np.amin(raw_line):
                yield ocr_record('', [], [])
                continue
            if line_normalization:
                # fail gracefully and return no recognition result in case the
                # input line can not be normalized.
                try:
                    box = dewarp(lnorm, box)
                except:
                    yield ocr_record('', [], [])
                    continue
            line = pil2array(box)
            line = lstm.prepare_line(line, pad)
        else:
            line = box
        pred = network.predictString(line)

        # calculate recognized LSTM locations of characters
        scale = len(raw_line.T)/(len(network.outputs)-2 * pad)
        result = lstm.translate_back_locations(network.outputs)
        pos = []
        conf = []

        for _, start, end, c in result:
            pos.append((coords[0] + int((start-pad)*scale), coords[1], coords[0] + int((end-pad/2)*scale), coords[3]))
            conf.append(c)
        if bidi_reordering:
            yield bidi_record(ocr_record(pred, pos, conf))
        else:
            yield ocr_record(pred, pos, conf)


def _rpred_clstm(net, im, bounds, pad, bidi_reordering):
    for box, coords in extract_boxes(im, bounds):
        if pad:
            colors = box.histogram()
            box = ImageOps.expand(
                box, border=(16, 0),
                fill=max(range(len(colors)), key=lambda x: colors[x]))
        char_infos = list(net.model.recognize_chars(box))
        pred = "".join(c.char for c in char_infos)
        pos = []
        conf = []
        for idx, c in enumerate(char_infos):
            if idx < len(char_infos)-1:
                rx = coords[0] + char_infos[idx+1].x_position - 1 - pad
            else:
                rx = coords[2]
            pos.append((coords[0] + c.x_position - pad,
                        coords[1],
                        rx,
                        coords[3]))
            conf.append(c.confidence)
        if bidi_reordering:
            yield bidi_record(ocr_record(pred, pos, conf))
        else:
            yield ocr_record(pred, pos, conf)
