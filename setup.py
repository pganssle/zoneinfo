import os

import setuptools
from setuptools import Extension

c_extension = Extension(
    "backports.zoneinfo._czoneinfo", sources=["lib/zoneinfo_module.c"],
)

setuptools.setup(ext_modules=[c_extension])

if "GCNO_TARGET_DIR" in os.environ:
    import glob

    gcno_files = glob.glob("**/*.gcno", recursive=True)

    if gcno_files:
        import shutil

        target_dir = os.environ["GCNO_TARGET_DIR"]
        os.makedirs(target_dir, exist_ok=True)
        for gcno_file in gcno_files:
            src = gcno_file
            src_dir, filename = os.path.split(gcno_file)
            new_target_dir = target_dir

            # When using gcc-9, the files are created in some flat location
            # with a naming convention where /path/to/file.gcda would be
            # represented as ${BASEDIR}/#path#to#file.gcda. In gcc-7, the input
            # directory is mirrored in the output directory, so the filename
            # would be ${BASEDIR}/path/to/file.gcda. The gcno files need to
            # have the same name and relative location as the gcda files,
            # apparently.
            if not filename.startswith("#"):
                rel_src_dir = os.path.relpath(src_dir)
                new_target_dir = os.path.join(target_dir, rel_src_dir)
                os.makedirs(new_target_dir, exist_ok=True)

            dst = os.path.join(new_target_dir, filename)
            shutil.copy(src, dst)
