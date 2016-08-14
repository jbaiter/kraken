#!/usr/bin/env python

from __future__ import absolute_import, division, print_function

import os

from setuptools import setup, Extension
from Cython.Build import cythonize


CLSTM_SOURCES = [
    'pyclstm.pyx', 'clstm.cc', 'clstm_prefab.cc', 'extras.cc',
    'batches.cc', 'ctc.cc', 'clstm_proto.cc', 'clstm.pb.cc',
    'clstm_compute.cc', 'tensor.cc']


clstm_ext = Extension(
    "pyclstm",
    sources=[os.path.join("./clstm", f) for f in CLSTM_SOURCES],
    include_dirs=['/usr/include/eigen3', '/usr/local/include/eigen3',
                  '/usr/local/include', '/usr/include',
                  '/usr/include/hdf5/serial', '/usr/include/hdf5'],
    libraries=['protobuf', 'png'],
    language='c++',
    extra_compile_args=['-w', '-std=c++11', '-Wno-unused-result', '-g',
                        '-DNODISPLAY=1', '-DTHROW=throw', '-DNDEBUG', '-Ofast',
                        '-DEIGEN_NO_DEBUG', '-finline', '-ffast-math',
                        '-fno-signaling-nans', '-funsafe-math-optimizations',
                        '-ffinite-math-only', '-march=native'])

setup(
    include_package_data=True,
    test_suite="nose.collector",
    tests_require="nose",
    setup_requires=['pbr'],
    ext_modules=cythonize([clstm_ext]),
    pbr=True,
)
