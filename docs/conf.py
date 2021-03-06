# Configuration file for the Sphinx documentation builder.
#
import sys, os

sys.path.insert(0, os.path.abspath(".."))
import unfurl

VERSION = unfurl.__version__()


# -- Project information -----------------------------------------------------

project = "Unfurl"
copyright = "2020, Adam Souzis"
author = "Adam Souzis"
release = VERSION

# -- General configuration ---------------------------------------------------

# Add any Sphinx extension module names here, as strings. They can be
# extensions coming with Sphinx (named 'sphinx.ext.*') or your custom
# ones.
extensions = [
    "recommonmark",
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.githubpages",
    "sphinx_click.ext",
    "sphinx-jsonschema",
    "sphinx.ext.autodoc.typehints",
    "sphinxcontrib.documentedlist",
    "sphinx.ext.autosectionlabel",
    "sphinx.ext.extlinks",
]

autodoc_typehints = "description"
modindex_common_prefix = ["unfurl."]

# :unfurl_site:`title <page>` or :unfurl_site:`page`
extlinks = {
    "onecommons": ("https://onecommons.org/%s", None),
    "unfurl_site": ("https://unfurl.run/%s", None),
    "tosca_spec": (
        "https://docs.oasis-open.org/tosca/TOSCA-Simple-Profile-YAML/v1.3/os/TOSCA-Simple-Profile-YAML-v1.3-os.html#%s",
        "TOSCA 1.3 Specification",
    ),
}

rst_epilog = """
.. _How it works: https://unfurl.run/howitworks.html
"""


# Add any paths that contain templates here, relative to this directory.
templates_path = ["_templates"]

default_role = "any"

# List of patterns, relative to source directory, that match files and
# directories to ignore when looking for source files.
# This pattern also affects html_static_path and html_extra_path.
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store", "todo"]


# -- Options for HTML output -------------------------------------------------
# see https://github.com/myyasuda/sphinxbootstrap4theme#html-theme-options

# The theme to use for HTML and HTML Help pages.  See the documentation for
# a list of builtin themes.
html_theme_options = {
    "show_sidebar": True,
    # "navbar_bg_class": 'faded',
    # "navbar_color_class": 'light',
    "sidebar_fixed": False,
    "main_width": "90%",
}

# default theme:
# html_theme = "alabaster"

import sphinxbootstrap4theme

html_theme = "sphinxbootstrap4theme"
html_theme_path = [sphinxbootstrap4theme.get_path()]

# Add any paths that contain custom static files (such as style sheets) here,
# relative to this directory. They are copied after the builtin static files,
# so a file named "default.css" will overwrite the builtin "default.css".
html_static_path = ["_static"]
html_css_files = ["custom.css"]
html_js_files = ["custom.js"]
html_logo = "./unfurl_logo.svg"
html_favicon = "favicon32.png"

# default: {"**":['globaltoc.html', 'sourcelink.html', 'searchbox.html'],
html_sidebars = {"**": ["globaltoc.html"], "index": []}
html_title = "Unfurl Documentation"
# XXX fix search
