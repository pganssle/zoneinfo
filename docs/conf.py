# Configuration file for the Sphinx documentation builder.
#
# This file only contains a selection of the most common options. For a full
# list see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

# -- Project information -----------------------------------------------------

project = "backports.zoneinfo"
author = "Paul Ganssle"
copyright = f"2020, {author}"

# Read the version information from the _version.py file
def get_version():
    import ast

    version_line = None
    with open("../src/backports/zoneinfo/_version.py") as f:
        for line in f:
            if line.startswith("__version__ ="):
                version_line = line
                break

    if version_line is None:
        raise ValueError("Version not found!")

    version_str = version_line.split("=", 1)[1].strip()

    return ast.literal_eval(version_str)


version = get_version()

# -- General configuration ---------------------------------------------------

# Add any Sphinx extension module names here, as strings. They can be
# extensions coming with Sphinx (named 'sphinx.ext.*') or your custom
# ones.
extensions = ["sphinx.ext.intersphinx"]

# Add any paths that contain templates here, relative to this directory.
templates_path = ["_templates"]

# List of patterns, relative to source directory, that match files and
# directories to ignore when looking for source files.
# This pattern also affects html_static_path and html_extra_path.
exclude_patterns = ["_output", "_build", "Thumbs.db", ".DS_Store"]


# -- Options for HTML output -------------------------------------------------

# The theme to use for HTML and HTML Help pages.  See the documentation for
# a list of builtin themes.
#
html_theme = "nature"

# Add any paths that contain custom static files (such as style sheets) here,
# relative to this directory. They are copied after the builtin static files,
# so a file named "default.css" will overwrite the builtin "default.css".
html_static_path = []

# For cross-links to other documentation
intersphinx_mapping = {"python": ("https://docs.python.org/3.9", None)}
