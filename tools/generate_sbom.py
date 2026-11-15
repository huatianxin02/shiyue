#!/usr/bin/env python3
"""Generate a deterministic CycloneDX inventory from the checked-in locks."""
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
vendor=json.loads((ROOT/'vendor-lock.json').read_text())
runtime=json.loads((ROOT/'runtime-lock.json').read_text())
node=json.loads((ROOT/'package-lock.json').read_text())
components=[]
for name,version in vendor['packages'].items():
    component={'type':'library','name':name,'version':version,'purl':f'pkg:pypi/{name}@{version}',
               'licenses':[{'license':{'id':'MIT' if name=='jieba' else 'BSD-3-Clause'}}]}
    source=vendor['sources'][name]
    component['hashes']=[{'alg':'SHA-256','content':source['sha256']}]
    component['externalReferences']=[{'type':'distribution','url':source['url']}]
    components.append(component)
components.append({'type':'application','name':'CPython (python-build-standalone)','version':'3.13.18+20261026',
    'licenses':[{'license':{'id':'Python-2.0'}}],
    'description':'Includes standard library and runtime dependencies; full license corpus retained in THIRD_PARTY_LICENSES.',
    'hashes':[{'alg':'SHA-256','content':runtime['digest'].split(':')[1]}],
    'externalReferences':[{'type':'distribution','url':runtime['browser_download_url']}]})
for path,entry in sorted(node['packages'].items()):
    if not path:continue
    name=path.rsplit('node_modules/',1)[-1]
    c={'type':'library','name':name,'version':entry['version'],'scope':'optional',
       'purl':f'pkg:npm/{name.replace("@","%40")}@{entry["version"]}',
       'properties':[{'name':'shiyue:usage','value':'development-test-only'}]}
    if entry.get('license'):c['licenses']=[{'expression':entry['license']}]
    components.append(c)
bom={'bomFormat':'CycloneDX','specVersion':'1.6','version':1,
     'metadata':{'component':{'type':'application','name':'Shiyue','version':node['version'],
                              'licenses':[{'license':{'id':'MIT'}}]}},'components':components}
(ROOT/'SBOM.cdx.json').write_text(json.dumps(bom,ensure_ascii=False,indent=2)+'\n')
print('SBOM components:',len(components))
