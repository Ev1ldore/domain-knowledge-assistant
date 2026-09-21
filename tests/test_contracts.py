"""Mock-based failure/contract checks. These are NOT real model E2E tests."""
from dataclasses import replace
from pathlib import Path
import json
import tempfile
import time
import unittest
from fastapi.testclient import TestClient
from app import create_app
from base.config import Settings
from new_main import IntegratedQASystem
from rag_qa.core.documents import chunks

class FakeModels:
    fingerprint='mock-embedding'
    def embeddings(self,texts): return [[1.,0.] for t in texts]
    def rerank(self,q,texts): return [0.99]*len(texts)
    def select_evidence(self,q,history,evidence):
        self.last_history=history
        if q=='invent': return {'status':'answerable','selections':[{'id':'fabricated','quote':'invented'}]}
        if q=='unsupported': return {'status':'insufficient','selections':[]}
        if q=='clarify': return {'status':'clarify','selections':[]}
        if q=='delete during generation':
            self.engine.kb.delete(evidence[0]['file_id'])
        return {'status':'answerable','selections':[{'id':evidence[0]['id'],'quote':evidence[0]['text']}]}

    def evidence_llm(self,prompt,system_prompt=None):
        data=json.loads(prompt)
        if '独立证据审核器' in system_prompt:
            yield json.dumps({'verdicts':[{'claim_id':c['claim_id'],'status':'supported','reason':'Mock'} for c in data['claims']]});return
        if '冲突兼容性审核器' in system_prompt:
            yield json.dumps({'relation':'incompatible'});return
        docs=[{'id':e['evidence_id'],'file_id':e['document_id'],'text':e['quote']} for e in data['evidence']]
        ref=data.get('reference_question_only') or ''
        history=ref.split('\n')[:-1]
        selected=self.select_evidence(data['questions'][0],history,docs)
        yield json.dumps({'claims':[{'question_index':0,'text':x['quote'],'subject':'测试工具','attribute':'限制','value':x['quote'],
            'conditions':'','kind':'fact','evidence_ids':[x['id']],'quotes':{x['id']:x['quote']}}
            for x in selected.get('selections',[])]})

class Contracts(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.settings=replace(Settings.load(),data_dir=Path(self.tmp.name),admin_password='test-only-password',reranker_backend='cross_encoder')
        self.models=FakeModels(); self.context=TestClient(create_app(self.settings,self.models));self.client=self.context.__enter__()
        self.engine=self.client.app.state.qa;self.models.engine=self.engine
        login=self.client.post('/api/kb/login',json={'username':'admin','password':'test-only-password'}).json()
        self.headers={'Authorization':'Bearer '+login['token']}
        self.sid=self.client.post('/api/create_session').json()['session_id']
    def tearDown(self): self.context.__exit__(None,None,None);self.tmp.cleanup()
    def ask(self,q,sid=None): return self.client.post('/api/query',json={'query':q,'session_id':sid or self.sid}).json()
    def upload(self,name='manual.md',text='# 测试章节\n测试工具允许导入三份资料。'):
        r=self.client.post('/api/kb/upload',headers=self.headers,files={'file':(name,text.encode())})
        self.assertEqual(r.status_code,202)
        fid=r.json()['id']
        for _ in range(200):
            files=self.client.get('/api/kb/files',headers=self.headers).json()['files']
            f=next(f for f in files if f['id']==fid)
            if f['status'] not in {'uploaded','indexing'}:break
            time.sleep(.01)
        self.assertEqual(f['status'],'ready',f);return fid
    def test_empty_faq_and_history(self):
        self.assertEqual(self.ask('工具的限制是什么？')['status'],'empty')
        data=json.dumps([{'question':'工具是什么？','answer':'测试工具。'}]).encode()
        for _ in range(2):
            r=self.client.post('/api/kb/faq/import',headers=self.headers,files={'file':('faq.json',data)})
            self.assertEqual(r.status_code,200)
        answer=self.ask('工具是什么？');self.assertEqual(answer['status'],'answered');self.assertEqual(answer['sources'][0]['type'],'faq')
        self.assertEqual(self.engine.store.status()['faq_count'],1)
        self.assertIn('测试工具。',self.client.get('/api/history/'+self.sid).json()['history'][-1]['answer'])
    def test_auth_all_management(self):
        for method,url in [('get','/api/kb/files'),('post','/api/kb/upload'),('post','/api/kb/faq/import'),('delete','/api/kb/files/missing'),('post','/api/kb/files/missing/rebuild'),('post','/api/kb/logout')]:
            self.assertEqual(getattr(self.client,method)(url).status_code,401,(method,url))
        self.client.post('/api/kb/logout',headers=self.headers)
        self.assertEqual(self.client.get('/api/kb/files',headers=self.headers).status_code,401)
    def test_no_default_password(self):
        with TestClient(create_app(replace(self.settings,admin_password=''),FakeModels())) as client:
            self.assertEqual(client.post('/api/kb/login',json={'username':'admin','password':''}).status_code,503)
    def test_input_and_removed_filter(self):
        for body in [{'query':'  '},{'query':'a','source_filter':'ai'},{'query':42},{'query':'a'*2001}]:
            self.assertIn(self.client.post('/api/query',json=body).status_code,[400,422])
        with self.client.websocket_connect('/api/stream') as ws:
            ws.send_json({'query':'a','source_filter':'ai'});self.assertEqual(ws.receive_json()['type'],'error')
        self.assertEqual(self.client.get('/api/sources').status_code,404)
    def test_greeting_only_full_question(self):
        self.assertEqual(self.ask('你好')['status'],'greeting')
        self.assertEqual(self.ask('你好，工具怎么使用？')['status'],'empty')
    def test_no_fabricated_citations(self):
        self.upload();result=self.ask('invent');self.assertEqual(result['status'],'insufficient');self.assertEqual(result['sources'],[])
        self.assertNotIn('invented',result['answer'])
    def test_revision_guards_inflight_generation(self):
        self.upload();r=self.ask('delete during generation');self.assertEqual(r['status'],'error');self.assertIn('更新',r['answer'])
        self.assertEqual(self.engine.store.history(self.sid),[])
    def test_delete_history_and_no_cache(self):
        fid=self.upload();first=self.ask('工具的限制是什么？');self.assertEqual(first['status'],'answered')
        self.assertEqual(first['sources'][0]['section'],'测试章节');self.assertNotIn('page',first['sources'][0])
        self.assertEqual(self.client.delete('/api/kb/files/'+fid,headers=self.headers).status_code,200)
        self.assertEqual(self.ask('工具的限制是什么？')['status'],'empty')
        rows=self.client.get('/api/history/'+self.sid).json()['history'];self.assertEqual(rows[0]['sources'],[]);self.assertNotIn('三份',rows[0]['answer'])
    def test_sessions_context_and_storage_restart(self):
        self.upload();self.ask('工具的限制是什么？');r=self.ask('那它呢？');self.assertEqual(self.models.last_history,['工具的限制是什么？'])
        other=self.client.post('/api/create_session').json()['session_id']
        self.assertEqual(self.ask('那它呢？',other)['status'],'clarify')
        with TestClient(create_app(self.settings,FakeModels())) as client:
            rows=client.get('/api/history/'+self.sid).json()['history'];self.assertEqual(len(rows),2)
    def test_stream_contract(self):
        self.upload();events=[]
        with self.client.websocket_connect('/api/stream') as ws:
            ws.send_json({'query':'工具的限制是什么？','session_id':self.sid})
            while True:
                event=ws.receive_json();events.append(event)
                if event['type'] in {'end','error'}:break
        self.assertEqual(events[0]['type'],'start');self.assertEqual(events[-1]['type'],'end')
        self.assertEqual(''.join(e.get('token','') for e in events),events[-1]['answer'])
        self.assertEqual(len(self.engine.store.history(self.sid)),1)
    def test_atomic_faq_import(self):
        data=json.dumps([{'question':'Q','answer':'A'},{'question':'','answer':'bad'}]).encode()
        r=self.client.post('/api/kb/faq/import',headers=self.headers,files={'file':('faq.json',data)})
        self.assertEqual(r.status_code,400);self.assertEqual(self.engine.store.status()['faq_count'],0)
    def test_file_collision_and_path(self):
        self.upload()
        r=self.client.post('/api/kb/upload',headers=self.headers,files={'file':('manual.md',b'overwrite')})
        self.assertEqual(r.status_code,400)
        r=self.client.post('/api/kb/upload',headers=self.headers,files={'file':('../escape.txt',b'escape')})
        self.assertEqual(r.status_code,400)
    def test_followup_does_not_skip_unsuccessful_topic(self):
        self.upload();self.ask('工具的限制是什么？');self.ask('unsupported')
        self.assertEqual(self.ask('那它呢？')['status'],'clarify')
    def test_model_failure_is_not_saved(self):
        self.upload()
        def broken(*args): raise RuntimeError('provider failure')
        self.models.select_evidence=broken
        self.assertEqual(self.ask('问题')['status'],'error')
        self.assertEqual(self.engine.store.history(self.sid),[])
    def test_interrupted_index_requires_explicit_rebuild(self):
        self.upload()
        with self.engine.store.connection() as db: db.execute("UPDATE files SET status='indexing'")
        from base.storage import Store
        reopened=Store(self.settings.data_dir)
        self.assertEqual(reopened.status()['failed_files'],1)
        self.assertEqual(reopened.status()['chunk_count'],0)
    def test_unauthorized_websocket_origin(self):
        from starlette.websockets import WebSocketDisconnect
        with self.assertRaises(WebSocketDisconnect):
            with self.client.websocket_connect('/api/stream',headers={'Origin':'https://unexpected.example'}):pass
    def test_pdf_page_metadata(self):
        import fitz
        path=Path(self.tmp.name)/'p.pdf'
        d=fitz.open();d.new_page().insert_text((72,72),'Actual page one');d.new_page().insert_text((72,72),'Actual page two');d.save(path);d.close()
        result=chunks(path,'pdf-test',self.settings);self.assertEqual([r['metadata']['page'] for r in result],[1,2])

if __name__=='__main__': unittest.main()
