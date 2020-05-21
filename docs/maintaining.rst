Maintainer's Guide
==================

Although this was the original implementation of the ``zoneinfo`` module, after
Python 3.9, it is now a backport, and to the extent that there is a "canonical"
repository, the `CPython repository <https://github.com/python/cpython>`_ has a
stronger claim than this one. Accepting outside PRs against this repository is
difficult because we are not set up to collect CLAs for CPython. It is easier
to accept PRs against CPython and import them here if possible.

The code layout is very different between the two, and unfortunately (partially
because of the different layouts, and the different module names), the code has
diverged, so keeping the two in sync is not as simple as copy-pasting one into
the other. For now, the two will need to be kept in sync manually.


Development environment
-----------------------

Maintenance scripts, releases, and tests are orchestrated using |tox|_
environments to manage the requirements of each script. The details of each
environment can be found in the ``tox.ini`` file in the repository root.

The repository also has pre-commit configured to automatically enforce various
code formatting rules on commit. To use it, install `pre-commit
<https://pre-commit.com/>`_ and run ``pre-commit install`` in the repository
root to install the git commit hooks.


Making a release
----------------

Releases are automated via the ``build-release.yml`` GitHub Actions workflow.
The project is built on every push; whenever a *tag* is pushed, the build
artifacts are released to `Test PyPI <https://test.pypi.org>`_, and when a
GitHub release is made, the project is built and released to `PyPI
<https://pypi.org>`_ (this is a workaround for the lack of "draft releases"
on PyPI, and the two actions can be unified when that feature is added).

To make a release:

1. Update the version number in ``src/backports/zoneinfo/_version.py`` and
   make a PR (if you want to be cautious, start with a ``.devN`` release
   intended only for PyPI).
2. Tag the repository with the current version – you can use the
   ``scripts/tag_release.sh`` script in the repository root to source the
   version automatically from the current module version.
3. Push the tag to GitHub (e.g. ``git push upstream 0.1.0.dev0``). This will
   trigger a release to Test PyPI. The PR does not need to be merged at this
   point if you are only planning to release to TestPyPI, but any "test only"
   tags should be deleted when the process is complete.
4. Wait for the GitHub action to succeed, then check the results on
   https://test.pypi.org/project/backports.zoneinfo .
5. If everything looks good, go into the GitHub repository's `"releases" tab
   <https://github.com/pganssle/zoneinfo/releases>`_ and click "Draft a new
   release"; type the name of the tag into the box, fill out the remainder of
   the form, and click "Publish". (Only do this step for non-dev releases).
6. Check that the release action has succeeded, then check that everything
   looks OK on https://pypi.org/project/backports.zoneinfo/ .

If there's a problem with the release, make a post release by appending
``.postN`` to the current version, e.g. ``0.1.1`` → ``0.1.1.post0``. If the
problem is sufficiently serious, yank the broken version.

.. Links
.. |tox| replace:: ``tox``
.. _tox: https://tox.readthedocs.io/en/latest/
