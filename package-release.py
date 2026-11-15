#!/usr/bin/env python3
"""Public-only packaging entrypoint. Personal QA/data are never included."""
from pathlib import Path
import runpy,sys
if len(sys.argv)==1:sys.argv+=['--source','--app','--output','public-release']
runpy.run_path(str(Path(__file__).resolve().parent/'tools/public_release.py'),run_name='__main__')
