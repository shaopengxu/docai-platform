import urllib.request
from pathlib import Path
import subprocess
import os

print("Creating a dummy doc for testing is non-trivial so let's check textutil options.")
result = subprocess.run(["textutil", "-help"], capture_output=True, text=True)
print(result.stdout)
