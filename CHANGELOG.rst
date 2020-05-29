Version 0.2.0 (2020-05-29)
==========================

- Added support for PyPy 3.6 (:gh-pr:`74`); when installed on PyPy, the library
  will not use the C extension, since benchmarks indicate that the pure Python
  implementation is faster.


Version 0.1.0 (2020-05-26)
==========================

This is the first public release of ``backports.zoneinfo``. It contains all the
features from the ``zoneinfo`` release in Python 3.9.0b1, with the following
changes:

- Added support for Python 3.6, 3.7 and 3.8 (:gh-pr:`69`, :gh-pr:`70`).
- The module is in the ``backports`` namespace rather than ``zoneinfo``.
- There is no support for compile-time configuration of ``TZPATH``.
- Fixed use-after-free in the ``module_free`` function (:bpo:`40705`,
  :gh-pr:`69`).
- Minor refactoring to the C extension to avoid compiler warnings
  (:bpo:`40686`, :bpo:`40714`, :cpython-pr:`20342`, :gh-pr:`72`).
- Removed unused imports, unused variables and other minor de-linting
  (:gh-pr:`71`).
