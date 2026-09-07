# Configuration file for the Sphinx documentation builder.
#
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

# -- Project information -----------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#project-information

project = 'kunene'
copyright = '2026, Willem Roux'
author = 'Willem Roux'
release = '1.3.0'

# ---
import sys, os

# Document the kunene next to these docs, not whatever copy happens to be
# installed in the environment: without this, autodoc imports the installed
# package (a different checkout) and the API pages describe the wrong code.
sys.path.insert(0, os.path.abspath('..'))

# -- General configuration ---------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#general-configuration

extensions = [
    'sphinx.ext.autodoc',
    'sphinx.ext.napoleon',  # Optional: for Google/NumPy style docstrings
    #'sphinx.ext.viewcode',  # Optional: adds source code links
]

templates_path = ['_templates']
exclude_patterns = ['_build', 'Thumbs.db', '.DS_Store']



# -- Options for HTML output -------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#options-for-html-output

#html_theme = 'alabaster'
html_theme = 'classic'
html_static_path = ['_static']

html_sidebars = {
    '**': [
        'globaltoc.html',   # Shows the global TOC across all pages
        #'localtoc.html',    # Optional: Shows headings for the current page
        'searchbox.html',   # Optional: Adds the search input bar
    ]
}
