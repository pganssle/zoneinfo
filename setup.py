import setuptools
from setuptools import Extension

c_extension = Extension(
    "zoneinfo._czoneinfo", sources=["lib/zoneinfo_module.c"],
)

setuptools.setup(ext_modules=[c_extension])
