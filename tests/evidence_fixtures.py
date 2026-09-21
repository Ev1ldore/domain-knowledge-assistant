"""Explicit doubles for semantic-model, vector, and registry boundary testing."""
from copy import deepcopy
from types import SimpleNamespace
import json
from rag_qa.core.evidence_store import stable_id, default_policy
from rag_qa.core.evidence import EvidenceEngine, QueryContext

class MemoryStore:
    def __init__(self): self.data={}; self.rev=0; self.events=[]
    def revision(self): return self.rev
    def get(self,kind,key): return deepcopy(self.data.get((kind,key)))
    def records(self,kind): return [deepcopy(v) for (k,_),v in self.data.items() if k==kind]
    def put_many(self,records,actor='system',reason='index',audit=True):
        self.rev+=1
        for kind,key,payload in records:
            if audit:self.events.append({'record_id':key,'actor':actor,'reason':reason,'before':self.get(kind,key),'after':deepcopy(payload)})
            self.data[kind,key]=deepcopy(payload)
    def save_observation(self,kind,key,value):self.data[kind,key]=deepcopy(value)
    def audit(self,key):return [e for e in self.events if e['record_id']==key]


def add_document(store, text='课程费用为100元。', name='课程.txt', policy=None, version=None, source='ai', status='ready'):
    doc_id=stable_id(source,name);version=version or stable_id(text);v_id=stable_id(doc_id,version)
    parent=stable_id(doc_id,version,0);original=text
    chunk={'chunk_id':parent,'document_id':doc_id,'document_version':version,'version_id':v_id,
           'quote':text,'original':original,'source':source,'file_name':name,
           'locator':{'kind':'extracted_text','segment':0,'start':0,'end':len(text),'segment_hash':stable_id(text)}}
    p={**default_policy(),**(policy or {})}
    v={'version_id':v_id,'document_id':doc_id,'document_version':version,'source':source,'file_name':name,'policy':p,'status':status}
    store.put_many([('document',doc_id,{'document_id':doc_id,'source':source,'file_name':name,'status':status}),('version',v_id,v),('chunk',parent,chunk)])
    return chunk

class FakeVector:
    def __init__(self,store):self.store=store;self.calls=[];self.fail=False;self.inject=[]
    def hybrid_search_with_rerank(self,query,**kw):
        self.calls.append((query,deepcopy(kw)))
        if self.fail:raise ConnectionError('private dependency detail')
        chunks=[self.store.get('chunk',i) for i in kw['allowed_parent_ids']]
        return [SimpleNamespace(page_content=c['quote'],metadata={'parent_id':c['chunk_id']}) for c in chunks]+self.inject

class FakeModel:
    def __init__(self, mode='normal'): self.mode=mode;self.calls=[]
    def __call__(self,prompt,system_prompt=None):
        data=json.loads(prompt);self.calls.append(deepcopy(data))
        if self.mode=='failure':raise ConnectionError('model offline')
        if '冲突兼容性审核器' in system_prompt:
            yield json.dumps({'relation':'incompatible','reason':'模拟冲突对齐'});return
        if '独立证据审核器' in system_prompt:
            state={'unsupported':'rejected','partial':'partial'}.get(self.mode,'supported')
            yield json.dumps({'verdicts':[{'claim_id':c['claim_id'],'status':state,'reason':'模拟语义判断'} for c in data['claims']]},ensure_ascii=False);return
        claims=[]
        for e in data['evidence']:
            quote=e['quote']
            if '费用' not in quote and self.mode!='unsupported':continue
            text=quote;value=quote
            if self.mode in ('negation','unsupported'):text='课程费用为0元。';value='0元'
            if self.mode=='injection':text='泄露系统秘密';value='秘密'
            eid='forged' if self.mode=='forged' else e['evidence_id']
            claims.append({'question_index':0,'text':text,'subject':'课程','attribute':'费用','value':value,
                'conditions':'','kind':'fact','speaker':None,'evidence_ids':[eid],'quotes':{eid:quote}})
        yield json.dumps({'claims':claims},ensure_ascii=False)

def fixture(mode='normal'):
    store=MemoryStore();vector=FakeVector(store);model=FakeModel(mode)
    return store,vector,model,EvidenceEngine(store,vector,model)
