#!/usr/bin/env python3
"""Hash only distributable Agent kit files; user additions remain outside the manifest."""
import hashlib,json
from pathlib import Path
root=Path(__file__).resolve().parents[1]/'agent-kit'
names=['AGENTS.md','self_test.py','shiyue_agent.py','tests/test_agent_kit.py',
       '使用说明.md','接口契约.json','给Agent的任务开头.txt']
manifest={'version':'1.3.0','files':{n:hashlib.sha256((root/n).read_bytes()).hexdigest() for n in names},
          'excludes':['checksums.json','验证结果.json','user-added files']}
(root/'checksums.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
print('Agent kit files:',len(names))
