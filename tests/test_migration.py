"""SQLite/API migration checks with explicit model doubles, not real-model evidence."""
from tests import test_contracts as contracts
from tests.evidence_fixtures import FakeModel
from rag_qa.core.evidence_admin import apply_policy,decide,revoke
import json
import time

class Migration(contracts.Contracts):
    # Reuse setup helpers without inheriting duplicate contract tests below.
    def use_governance_model(self,mode='normal'):
        self.model=FakeModel(mode);self.engine.evidence.llm=self.model

    def wait_ready(self,fid):
        for _ in range(300):
            f=next(f for f in self.engine.kb.list_files() if f['id']==fid)
            if f['status'] not in {'uploaded','indexing'}:break
            time.sleep(.01)
        self.assertEqual(f['status'],'ready',f)

    def policy(self,fid,**changes):
        v=next(v for v in self.engine.evidence_store.records('version') if v['document_id']==fid)
        apply_policy(self.engine.evidence_store,v['version_id'],{**v['policy'],**changes},'admin','测试依据')
        return v

    def test_faq_private_policy_applies_before_publication(self):
        self.use_governance_model()
        data=json.dumps([{'question':'课程费用？','answer':'SECRET课程费用为999元。'}]).encode()
        r=self.client.post('/api/kb/faq/import',headers=self.headers,
            data={'policy':json.dumps({'permissions':{'visibility':'restricted'}})},files={'file':('faq.json',data)})
        self.assertEqual(r.status_code,200)
        result=self.ask('课程费用？');self.assertFalse(result['citations']);self.assertFalse(self.model.calls)
        self.assertNotIn('SECRET',str(result))

    def test_sqlite_scope_filter_before_reranker(self):
        self.use_governance_model();fid=self.upload('secret.txt','SECRET课程费用为999元。')
        self.policy(fid,permissions={'visibility':'restricted','users':['alice']})
        self.upload('public.txt','课程费用为100元。')
        seen=[];self.models.rerank=lambda q,texts:seen.extend(texts) or [.99]*len(texts)
        r=self.ask('课程费用？');self.assertEqual(r['overall_status'],'supported')
        self.assertNotIn('SECRET',str(r)+str(self.model.calls)+str(seen))
        self.assertFalse(r['cache']['enabled'])

    def test_permission_change_hides_historical_prose(self):
        self.use_governance_model();fid=self.upload('p.txt','课程费用为100元。');self.ask('费用？')
        self.policy(fid,permissions={'visibility':'restricted'})
        history=self.client.get('/api/history/'+self.sid).json()['history']
        self.assertNotIn('100元',str(history));self.assertFalse(self.ask('费用？')['citations'])

    def test_replace_preserves_id_versions_and_delete_tombstone(self):
        self.use_governance_model();fid=self.upload('p.txt','课程费用为100元。');self.ask('费用？')
        self.policy(fid,effective_to='2026-01-01')
        r=self.client.put('/api/kb/files/'+fid,headers=self.headers,files={'file':('p.txt','课程费用为200元。'.encode())})
        self.assertEqual(r.status_code,202);self.wait_ready(fid)
        versions=[v for v in self.engine.evidence_store.records('version') if v['document_id']==fid];self.assertEqual(len(versions),2)
        new=next(v for v in versions if not v['policy']['effective_to'])
        apply_policy(self.engine.evidence_store,new['version_id'],{**new['policy'],'effective_from':'2026-01-01'},'admin','新版生效公告')
        for day,want,absent in [('2025-12-31','100元','200元'),('2026-01-01','200元','100元')]:
            r=self.client.post('/api/query',json={'query':'课程费用？','as_of':day,'session_id':self.sid}).json()
            self.assertIn(want,r['answer']);self.assertNotIn(absent,r['answer'])
        self.client.delete('/api/kb/files/'+fid,headers=self.headers)
        self.assertEqual(self.engine.evidence_store.get('document',fid)['status'],'deleted')
        self.assertEqual(len(self.engine.evidence_store.records('chunk')),2)
        self.assertFalse(self.ask('费用？')['citations'])

    def test_region_api_and_upload_policy_no_public_window(self):
        self.use_governance_model()
        for region,cost in [('上海','100'),('北京','200')]:
            r=self.client.post('/api/kb/upload',headers=self.headers,data={'policy':json.dumps({'scope':{'region':region}})},
                files={'file':(region+'.txt',('课程费用为'+cost+'元。').encode())})
            self.wait_ready(r.json()['id'])
        self.assertEqual(self.ask('费用？')['overall_status'],'needs_clarification')
        r=self.client.post('/api/query',json={'query':'费用？','scope':{'region':'上海'}}).json()
        self.assertEqual(r['overall_status'],'supported');self.assertNotIn('200元',r['answer'])

    def test_sqlite_reversible_admin_decision_audit(self):
        self.use_governance_model();self.upload('a.txt','课程费用为100元。');self.upload('b.txt','课程费用为200元。')
        first=self.ask('费用？');self.assertEqual(first['overall_status'],'conflicted');g=first['conflicts'][0]
        base='/api/kb/evidence/conflicts/'+g['conflict_group']+'/decision'
        self.assertEqual(self.client.post(base,json={'chosen_claim_id':g['claim_ids'][0],'basis':'公告'}).status_code,401)
        r=self.client.post(base,headers=self.headers,json={'chosen_claim_id':g['claim_ids'][0],'basis':'人工核验公告'})
        self.assertEqual(r.status_code,200);self.assertEqual(self.ask('费用？')['conflicts'][0]['status'],'adjudicated')
        self.assertEqual(self.client.delete(base,headers=self.headers).status_code,200)
        self.assertEqual(self.ask('费用？')['overall_status'],'conflicted')
        events=self.client.get('/api/kb/evidence/audit/'+g['conflict_group'],headers=self.headers).json()['events']
        self.assertEqual(len(events),2);self.assertEqual(events[0]['actor'],'admin');self.assertNotIn('Bearer',str(events))

    def test_migration_backfills_exact_legacy_locator_idempotently(self):
        self.use_governance_model();fid=self.upload('legacy.txt','课程费用为100元。')
        with self.engine.store.connection() as db:
            db.execute("UPDATE chunks SET metadata='{}'")
            db.execute('DELETE FROM evidence_records');db.execute('DELETE FROM evidence_vectors')
        from rag_qa.core.evidence_store import EvidenceStore
        e=EvidenceStore(self.engine.store,self.settings);rev=e.revision()
        again=EvidenceStore(self.engine.store,self.settings)
        self.assertEqual(rev,again.revision())
        self.assertEqual(self.ask('费用？')['citations'][0]['citation_validity'],'valid')

    def test_legacy_missing_original_unresolved(self):
        fid=self.upload('legacy.txt','课程费用为100元。')
        with self.engine.store.connection() as db:
            db.execute("UPDATE chunks SET metadata='{}'");db.execute("UPDATE files SET path='/missing/original'")
            db.execute('DELETE FROM evidence_records');db.execute('DELETE FROM evidence_vectors')
        from rag_qa.core.evidence_store import EvidenceStore
        EvidenceStore(self.engine.store,self.settings)
        r=self.ask('费用？');self.assertFalse(r['citations']);self.assertEqual(r['retrieval_status']['index_status'],'unindexed')

    def test_error_final_event_preserves_dependency_status(self):
        self.use_governance_model('failure');self.upload('p.txt','课程费用为100元。')
        with self.client.websocket_connect('/api/stream') as ws:
            ws.send_json({'query':'费用？','session_id':self.sid});events=[]
            while True:
                e=ws.receive_json();events.append(e)
                if e['type']=='end':break
        self.assertEqual(events[0]['validation_status'],'pending');self.assertEqual(events[-1]['status'],'error')
        self.assertEqual(events[-1]['retrieval_status']['status'],'model_error');self.assertFalse(events[-1]['citations'])
        self.assertFalse(any(e['type']=='token' for e in events))

# Do not rerun inherited test methods in this suite.
for _name in list(vars(contracts.Contracts)):
    if _name.startswith('test_'):setattr(Migration,_name,None)
