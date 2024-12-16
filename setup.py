# setup.py

import sys
import os
from setuptools import setup, find_packages
import delete_me_discord
# Ensure Python 3.6+
if sys.version_info < (3, 6):
    sys.exit("ERROR: delete-me-discord requires Python 3.6 or higher.")

# Read the long description from README.md
def read_long_description():
    here = os.path.abspath(os.path.dirname(__file__))
    try:
        with open(os.path.join(here, "README.md"), encoding="utf-8") as fh:
            return fh.read()
    except FileNotFoundError:
        return "A tool to delete Discord messages."


setup(
    name="delete-me-discord",
    version=delete_me_discord.__version__,
    packages=find_packages(exclude=["tests"]),
    author="Jan T. Müller",
    author_email="mail@jantmueller.com",
    description="A tool to delete Discord messages.",
    long_description=read_long_description(),
    long_description_content_type="text/markdown",
    url="https://github.com/janthmueller/delete-me-discord",
    project_urls={
        "Documentation": "https://github.com/janthmueller/delete-me-discord/blob/main/README.md",
        "Source": "https://github.com/janthmueller/delete-me-discord",
        "Tracker": "https://github.com/janthmueller/delete-me-discord/issues",
    },
    license="MIT",  # Replace with your license if different
    classifiers=[
        "Intended Audience :: Developers",
        "Topic :: Communications :: Chat",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.6",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
    ],
    python_requires=">=3.6",
    install_requires=[
        "types-requests",
        "python-dotenv",
    ],
    entry_points={
        "console_scripts": [
            "delete-me-discord=delete_me_discord:main",
        ],
    },
)
