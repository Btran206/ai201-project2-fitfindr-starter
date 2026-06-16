import sys
import os

# Ensure the project root is on sys.path so tests in subdirectories can import
# tools, utils, etc. without needing relative imports or installed packages.
sys.path.insert(0, os.path.dirname(__file__))
