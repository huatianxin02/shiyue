"""Run the portable suite against source; packaged self-test uses App runtime."""
import importlib.util
import os
from pathlib import Path
os.environ.setdefault('SHIYUE_TEST_APP','/Applications/拾页资料库.app')
project=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('portable_agent_tests',project/'agent-kit/tests/test_agent_kit.py')
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.PROJECT=project
AgentKitTests=module.AgentKitTests
