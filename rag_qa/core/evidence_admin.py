"""Admin-only, reversible metadata and conflict decisions using existing login."""
from datetime import date
from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field
from .evidence_store import default_policy, utcnow


class PolicyRequest(BaseModel):
    policy: dict
    reason: str = Field(min_length=1, max_length=1000)


class DecisionRequest(BaseModel):
    chosen_claim_id: str
    basis: str = Field(min_length=1, max_length=1000)


def validate_policy(p):
    if not isinstance(p, dict) or set(p) - set(default_policy()):
        raise ValueError('未知政策字段')
    merged = {**default_policy(), **p}
    for name in ('published_at','updated_at','effective_from','effective_to','revoked_at','superseded_at'):
        if merged[name] is not None:
            date.fromisoformat(merged[name])
    if merged['effective_from'] and merged['effective_to'] and merged['effective_from'] >= merged['effective_to']:
        raise ValueError('失效时间必须晚于生效时间（失效边界不包含）')
    if merged['superseded_by'] or merged['superseded_at'] or merged['revoked_at']:
        if not merged['replacement_basis']:
            raise ValueError('替代、撤销或失效必须提供依据')
    if bool(merged['superseded_by']) != bool(merged['superseded_at']):
        raise ValueError('替代关系必须同时提供目标版本和生效日期')
    acl = merged['permissions']
    if not isinstance(acl, dict) or acl.get('visibility') not in ('public','restricted'):
        raise ValueError('必须明确 visibility=public/restricted')
    if set(acl) - {'visibility','tenant','users','roles'}:
        raise ValueError('未知权限字段')
    for name in ('users','roles'):
        if name in acl and (not isinstance(acl[name], list) or any(not isinstance(x,str) for x in acl[name])):
            raise ValueError('权限名单必须为字符串列表')
    if acl.get('tenant') is not None and not isinstance(acl['tenant'],str):
        raise ValueError('tenant 必须为字符串或空')
    scope = merged['scope']
    if not isinstance(scope, dict) or len(scope)>12 or any(not isinstance(k,str) or not isinstance(v,str) for k,v in scope.items()):
        raise ValueError('scope 必须为字符串对象')
    authority = merged['authority']
    if not isinstance(authority,dict) or not isinstance(authority.get('level'),str):
        raise ValueError('authority 必须包含 level 和 basis')
    if authority['level'] != 'unknown' and not authority.get('basis'):
        raise ValueError('来源权威等级必须有判定依据')
    if merged['source_kind'] not in ('document','reported'):
        raise ValueError('source_kind 必须为 document/reported')
    if merged['source_kind'] == 'reported' and not merged['speaker']:
        raise ValueError('转述必须提供说话者')
    for k in ('source_name','source_url','origin_id','speaker','superseded_by','replacement_basis'):
        if merged[k] is not None and (not isinstance(merged[k],str) or len(merged[k])>2000):
            raise ValueError('来源字段必须为短字符串或空')
    if merged['source_url'] and not merged['source_url'].startswith(('https://','http://')):
        raise ValueError('来源地址必须为 HTTP(S)')
    return merged


def apply_policy(store, version_id, policy, actor, reason):
    policy = validate_policy(policy)
    v = store.get('version', version_id)
    if not v:
        raise ValueError('版本不存在')
    if policy['superseded_by'] and not store.get('version', policy['superseded_by']):
        raise ValueError('替代目标版本不存在')
    changes = [('version', version_id, {**v, 'policy':policy}),
               ('policy_change', version_id, {'before':v['policy'], 'after':policy, 'actor':actor, 'reason':reason})]
    doc = store.get('document', v['document_id'])
    # A permission restriction applies to all history, not just latest upload.
    doc = {**doc, 'permissions':policy['permissions']}
    changes.append(('document', v['document_id'], doc))
    for old in store.records('version'):
        if old['document_id'] == v['document_id'] and old['version_id'] != version_id:
            changes.append(('version', old['version_id'], {**old, 'policy':{
                **old['policy'], 'permissions': policy['permissions']}}))
    store.put_many(changes, actor, reason)


def undo_policy(store, version_id, actor):
    change = store.get('policy_change', version_id)
    version = store.get('version', version_id)
    if not change or not version or change['after'] != version['policy']:
        raise ValueError('没有可安全撤销的元数据修正')
    apply_policy(store, version_id, change['before'], actor, '撤销最近元数据修正')


def decide(store, group_id, chosen, basis, actor):
    group = store.get('conflict', group_id)
    if not group or chosen not in group['claim_ids']:
        raise ValueError('冲突或候选结论不存在')
    selected = next(s for s in group['statements'] if s['claim_id'] == chosen)
    store.put_many([('decision', group_id, {'conflict_group':group_id, 'chosen_claim_id':chosen,
        'chosen_evidence_ids':selected['evidence_ids'], 'chosen_value':selected['value'],
        'basis':basis, 'actor':actor, 'decided_at':utcnow(), 'revoked':False})], actor, basis)


def revoke(store, group_id, actor):
    old = store.get('decision', group_id)
    if not old:
        raise ValueError('裁决不存在')
    store.put_many([('decision',group_id,{**old,'revoked':True,'revoked_by':actor,'revoked_at':utcnow()})],actor,'撤销裁决')


def install_admin_routes(app, get_store, require_admin):
    @app.get('/api/kb/evidence')
    async def evidence_records(admin=Depends(require_admin)):
        store = get_store()
        return {'versions':store.records('version'), 'conflicts':store.records('conflict'),
                'decisions':store.records('decision'), 'revision':store.revision()}

    @app.put('/api/kb/evidence/versions/{version_id}')
    async def policy(version_id: str, body: PolicyRequest, admin=Depends(require_admin)):
        store = get_store()
        try:
            apply_policy(store,version_id,body.policy,admin,body.reason)
            return {'status':'success'}
        except ValueError as e:
            raise HTTPException(400,str(e))

    @app.post('/api/kb/evidence/versions/{version_id}/undo')
    async def undo_metadata(version_id: str, admin=Depends(require_admin)):
        store = get_store()
        try:
            undo_policy(store, version_id, admin)
            return {'status':'success'}
        except ValueError as e:
            raise HTTPException(400,str(e))

    @app.post('/api/kb/evidence/conflicts/{group_id}/decision')
    async def decision(group_id: str, body: DecisionRequest, admin=Depends(require_admin)):
        store = get_store()
        try:
            decide(store,group_id,body.chosen_claim_id,body.basis,admin)
            return {'status':'success'}
        except ValueError as e:
            raise HTTPException(400,str(e))

    @app.delete('/api/kb/evidence/conflicts/{group_id}/decision')
    async def undo(group_id: str, admin=Depends(require_admin)):
        store = get_store()
        try:
            revoke(store,group_id,admin)
            return {'status':'success'}
        except ValueError as e:
            raise HTTPException(400,str(e))

    @app.get('/api/kb/evidence/audit/{record_id}')
    async def audit(record_id: str, admin=Depends(require_admin)):
        store = get_store()
        return {'events':store.audit(record_id)}
