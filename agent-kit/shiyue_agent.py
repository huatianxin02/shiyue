#!/usr/bin/env python3
"""Agent kit 1.3: authenticated APIs; self-test uses only a disposable library."""
import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlencode
sys.dont_write_bytecode = True


def ident(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{32}', value):
        raise ValueError('需要完整的 32 位文件 ID，不能用简称或哈希代替')
    return value


def compact(detail):
    fields = ('id','name','origin_path','sha','mode','source_id','source_name','source_kind',
              'tags','collections','preference','available','origin_missing','warning','size')
    return {key: detail.get(key) for key in fields}


def normal_names(values, limit):
    result = []
    for value in values:
        name = ' '.join(value.split()) if limit == 40 else value.strip()
        if not name or len(name) > limit or any(ord(c) < 32 for c in name):
            raise ValueError('分类或标签名称为空、过长或含控制字符')
        if name not in result: result.append(name)
    return result


def collection_path(value):
    path=json.loads(value)
    if not isinstance(path,list) or not 1<=len(path)<=9 or any(not isinstance(name,str) for name in path):
        raise ValueError('--collection-path 必须是 1–9 级名称的 JSON 数组')
    return [normal_names([name],40)[0] for name in path]


def require_tree(client):
    if client.request('/api/status').get('collection_max_depth')!=9:
        raise ValueError('层级分类接口需要运行拾页 1.8.0 或更高版本')


def require_write(client, capability):
    status = client.request('/api/status')
    if not re.fullmatch(r'1\.(?:9|10)\.\d+', status.get('version', '')) or status.get('capabilities', {}).get(capability) != 1:
        raise ValueError('当前接入包写入已验证范围为拾页 1.9.x / 1.10.x，且必须声明能力 '+capability+'；当前转为只读。请查看接入包说明和发行说明')


def classify(client, args):
    if args.apply: require_write(client, 'classify')
    expected = {}
    if args.receipt:
        with Path(args.receipt).open('rb') as stream: raw = stream.read(4_000_001)
        if len(raw) > 4_000_000: raise ValueError('回执过大')
        receipt = json.loads(raw)
        if (not isinstance(receipt,dict) or receipt.get('index_ready') is not True
                or receipt.get('success') is not True or receipt.get('complete') is not True
                or receipt.get('failure_count') != 0):
            raise ValueError('请先完成入库；回执须 complete/success/index_ready=true，failure_count=0')
        ids = []
        if not isinstance(receipt.get('files'),list):raise ValueError('回执缺少文件列表')
        for file in receipt['files']:
            if not isinstance(file,dict):raise ValueError('回执文件记录格式不正确')
            if file.get('current_version') is not True or file.get('index_ready') is not True:
                raise ValueError('回执中有未就绪或已过期的文件')
            fid = ident(file['id']);sha = file.get('sha256', file.get('sha'))
            if not isinstance(sha,str) or not re.fullmatch(r'[0-9a-f]{64}',sha):raise ValueError('回执缺少完整 SHA-256')
            if fid in expected and expected[fid]!=sha:raise ValueError('回执中的文件版本冲突')
            ids.append(fid);expected[fid] = sha
    else:
        ids = [ident(x) for x in args.id]
    ids = list(dict.fromkeys(ids))
    if not 1 <= len(ids) <= 1000:raise ValueError('每批选择 1–1000 个文件')
    collections = normal_names(args.collection, 40)
    tags = normal_names(args.tag, 80)
    paths = [collection_path(value) for value in args.collection_path]
    collection_ids = list(dict.fromkeys(ident(value) for value in args.collection_id))
    if paths: require_tree(client)
    if not collections and not paths and not collection_ids and not tags:raise ValueError('请指定至少一个 --collection 或 --tag')
    before = [client.request('/api/files/'+fid) for fid in ids]
    for detail in before:
        if detail['id'] in expected and detail['sha'] != expected[detail['id']]:
            raise ValueError('文件已更新，请获取新回执后再归类：'+detail['id'])
        if tags and detail['mode'] == 'read-only':raise ValueError('只读资料包不能修改标签')
        if len(set(detail['tags']+tags)) > 50:raise ValueError('合并后标签超过每文件 50 个')
    plan = {'ids':ids, 'add_collections':collections,'add_tags':tags, 'add_collection_paths':paths, 'add_collection_ids':collection_ids,
            'files':[compact(x) for x in before], 'preserve_existing_memberships':True,
            'notice':'仅修改分类/标签；不搬文件，不删除旧分类。多请求非原子事务，请串行操作同一批文件。'}
    if not args.apply:return {'ok':True,'applied':False,'plan':plan}
    changes = []
    try:
        targets = ([{'name':name} for name in collections]+[{'path':path} for path in paths]+[{'collection_id':cid} for cid in collection_ids])
        for target in targets:
            result = client.request('/api/collection/assign',{**target,'ids':ids,'action':'add'})
            changes.append({'collection':result['collection'],'changed':result['changed']})
        resolved_collections = {step['collection']['id'] for step in changes}
        for detail in before:
            if tags:
                # Refresh immediately before the replace-style API to retain existing tags.
                fresh = client.request('/api/files/'+detail['id'])
                if fresh['sha'] != detail['sha']:raise ValueError('归类期间资料版本变化，请重新核对')
                merged = list(dict.fromkeys(fresh['tags']+tags))
                if len(merged)>50:raise ValueError('合并后标签超过每文件 50 个')
                if merged != fresh['tags']:
                    client.request('/api/edit',{'id':detail['id'],'tags':merged})
                    changes.append({'file_id':detail['id'],'added_tags':tags})
        after = [client.request('/api/files/'+fid) for fid in ids]
        for prior,detail in zip(before,after):
            if detail['sha'] != prior['sha'] or not set(tags)<=set(detail['tags']):raise ValueError('归类后版本或标签复核失败')
            actual = {x['id'] for x in detail['collections']}
            if not resolved_collections<=actual or not {x['id'] for x in prior['collections']}<=actual:
                raise ValueError('分类归属复核失败')
            if not set(prior['tags'])<=set(detail['tags']):raise ValueError('原有标签复核失败')
        return {'ok':True,'applied':True,'verified':True,'changes':changes,'files':[compact(x) for x in after]}
    except (ValueError,OSError,KeyError,TypeError) as error:
        return {'ok':False,'applied':True,'verified':False,'error':str(error),'completed_steps':changes,
                'notice':'可能已经部分归类，请 inspect 复核；按原计划重试可安全补齐，不要删除或重新入库。'}


def run(args, client):
    if args.command == 'status':
        status = client.request('/api/status')
        return {'ok':True,'app_version':status['version'],'data_path':status['data_path'],
                'files':status['stats']['files'],'collections':status['collections'],
                'collection_max_depth':status.get('collection_max_depth',1),
                'capabilities':status.get('capabilities',{}), 'kit_version':'1.3.0',
                'write_compatible':bool(re.fullmatch(r'1\.(?:9|10)\.\d+',status['version'])) and status.get('capabilities',{}).get('classify')==1,
                'profiles':client.request('/api/knowledge/profiles')}
    if args.command == 'collection-create':
        require_write(client, 'collection_tree')
        require_tree(client)
        payload={'name':normal_names([args.name],40)[0],'parent_id':ident(args.parent_id) if args.parent_id else None}
        return {'ok':True,'collection':client.request('/api/collection/create',payload)}
    if args.command == 'collection-move':
        require_write(client, 'collection_move')
        require_tree(client)
        payload={'collection_id':ident(args.id),'parent_id':ident(args.parent_id) if args.parent_id else None}
        return {'ok':True,'collection':client.request('/api/collection/move',payload)}
    if args.command == 'inspect':return {'ok':True,'files':[compact(client.request('/api/files/'+ident(x))) for x in args.id]}
    if args.command == 'find':
        # Filename search narrows the corpus, then exact canonical origin path decides.
        target = str(Path(args.path).expanduser().resolve());offset=0;found=[];seen=set()
        name=Path(target).name
        # The server truncates a query at 200 characters, possibly inside an
        # unbroken token. In that rare case scan pages without a name filter.
        if len(name)>200:name=''
        while True:
            result=client.request('/api/files?'+urlencode({'q':name,'offset':offset}))
            for item in result['files']:
                if item['id'] in seen:continue
                seen.add(item['id'])
                if item['origin_path']==target:found.append(compact(client.request('/api/files/'+ident(item['id']))))
            offset += len(result['files'])
            if offset>=result['total'] or not result['files']:break
            if offset>100000:raise ValueError('同名匹配过多，请改用完整文件 ID')
        return {'ok':True,'canonical_path':target,'matches':found,'count':len(found),
                'notice':'按当前索引路径查找，不证明磁盘文件内容仍与索引一致。'}
    if args.command == 'classify':return classify(client,args)
    if args.command == 'prefer':
        source = client.request('/api/files/'+ident(args.id))
        target = client.request('/api/files/'+ident(args.preferred_id)) if args.preferred_id else None
        payload = {'file_id':source['id'], 'preferred_id':target['id'] if target else None,
                   'expected_sha':source['sha'], 'target_sha':target['sha'] if target else None, 'reason':args.reason}
        if not args.apply:return {'ok':True,'applied':False,'plan':payload,'source':compact(source),'target':compact(target) if target else None}
        require_write(client, 'file_preferences')
        return {'ok':True,'applied':True,**client.request('/api/files/preference',payload)}
    params = {'profile':args.profile}
    if args.command == 'search':
        params.update(q=args.query,limit=args.limit,include_children=str(not args.direct_only).lower(),include_paths=str(args.include_paths).lower())
        for name, values in [('collection_ids',[ident(x) for x in args.collection_id]), ('collection_paths',[collection_path(x) for x in args.collection_path]), ('tags',normal_names(args.tag,80))]:
            if values:params[name]=json.dumps(values,ensure_ascii=False)
        route='/api/knowledge/search'
    elif args.command == 'read':
        params.update(id=ident(args.id),start=args.start,limit=args.limit)
        if args.sha:params['expected_sha']=args.sha
        if args.extract:params['expected_extract']=args.extract
        route='/api/knowledge/read'
    else:
        params.update(id=ident(args.id),passage=args.passage,context=args.context);route='/api/knowledge/passage'
    return {'ok':True,**client.request(route+'?'+urlencode(params))}


def parser():
    p=argparse.ArgumentParser(description='拾页 Agent 辅助工具：现有接口的分类、标签与检索封装；不直接访问数据库')
    p.add_argument('--app',default='/Applications/拾页资料库.app')
    p.add_argument('--data',default=str(Path.home()/'Library/Application Support/拾页资料库'))
    sub=p.add_subparsers(dest='command',required=True)
    sub.add_parser('status')
    sub.add_parser('self-test',help='核对接入包校验值，在临时目录启动指定 App 的服务并自检；不连接真实库')
    find=sub.add_parser('find');find.add_argument('--path',required=True)
    inspect=sub.add_parser('inspect');inspect.add_argument('--id',action='append',required=True)
    classify=sub.add_parser('classify');group=classify.add_mutually_exclusive_group(required=True)
    group.add_argument('--id',action='append');group.add_argument('--receipt')
    classify.add_argument('--collection',action='append',default=[],help='顶级分类名称')
    classify.add_argument('--collection-path',action='append',default=[],help='完整路径 JSON 数组，例如 ["易学","经典","周易"]')
    classify.add_argument('--collection-id',action='append',default=[],help='既有分类的完整 ID')
    classify.add_argument('--tag',action='append',default=[])
    classify.add_argument('--apply',action='store_true',help='执行已获用户授权的归类；省略时仅输出计划')
    create=sub.add_parser('collection-create');create.add_argument('--name',required=True);create.add_argument('--parent-id')
    move=sub.add_parser('collection-move');move.add_argument('--id',required=True)
    destination=move.add_mutually_exclusive_group(required=True);destination.add_argument('--parent-id');destination.add_argument('--root',action='store_true')
    search=sub.add_parser('search');search.add_argument('--query',required=True);search.add_argument('--limit',type=int,default=8)
    search.add_argument('--collection-id',action='append',default=[])
    search.add_argument('--collection-path',action='append',default=[])
    search.add_argument('--tag',action='append',default=[])
    search.add_argument('--direct-only',action='store_true',help='只搜索分类本级')
    search.add_argument('--include-paths',action='store_true',help='返回当前 profile 内命中文件的索引路径')
    prefer=sub.add_parser('prefer',help='明确指定优先条目；只添加关系，不删除或隐藏旧证据')
    prefer.add_argument('--id',required=True)
    choice=prefer.add_mutually_exclusive_group(required=True);choice.add_argument('--preferred-id');choice.add_argument('--clear',action='store_true')
    prefer.add_argument('--reason',default='');prefer.add_argument('--apply',action='store_true')
    read=sub.add_parser('read');read.add_argument('--id',required=True);read.add_argument('--start',type=int,default=0)
    read.add_argument('--limit',type=int,default=3);read.add_argument('--sha');read.add_argument('--extract')
    passage=sub.add_parser('passage');passage.add_argument('--id',required=True);passage.add_argument('--passage',required=True)
    passage.add_argument('--context',type=int,default=1)
    for sp in (search,read,passage):sp.add_argument('--profile',required=True,help='必须明确指定已授权的读取范围 ID')
    return p


def main(argv=None):
    args=parser().parse_args(argv)
    try:
        if args.command=='self-test':
            from self_test import run_self_test
            result=run_self_test(Path(args.app).expanduser())
            print(json.dumps(result,ensure_ascii=False))
            return 0 if result.get('ok') else 2
        runtime=Path(args.app).expanduser()/'Contents/Resources/runtime/agent_ingest.py'
        if not runtime.is_file():raise ValueError('找不到拾页 1.7+ 的 agent_ingest.py，请用 --app 指定完整应用路径')
        spec=importlib.util.spec_from_file_location('shiyue_ingest_client',runtime)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        result=run(args,module.Client(args.data))
    except (ValueError,OSError,KeyError,TypeError) as error:
        result={'ok':False,'error':str(error)}
    print(json.dumps(result,ensure_ascii=False))
    return 0 if result.get('ok') else 2

if __name__=='__main__':raise SystemExit(main())
