"""Put the skill's scripts on sys.path for the tests (they import each other by module name)."""
import os
import sys

SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, os.path.abspath(SCRIPTS))
