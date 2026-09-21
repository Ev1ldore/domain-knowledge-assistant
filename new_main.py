"""One evidence-governed path for documents, FAQ, HTTP and WebSocket."""
import hashlib
import logging
import re
import threading
from base.storage import Store
from mysql_qa.faq import FAQRepository
from rag_qa.core.models import ModelGateway
from rag_qa.core.retriever import Retriever,EvidenceRetriever
from rag_qa.core.kb_manager import KBManager
from rag_qa.core.evidence_store import EvidenceStore
from rag_qa.core.evidence import EvidenceEngine,normalize_context,empty_result

log=logging.getLogger(__name__)

class IntegratedQASystem:
    def __init__(self,settings,models=None):
        self.s=settings;self.store=Store(settings.data_dir)
        self.evidence_store=EvidenceStore(self.store,settings);self.store.evidence=self.evidence_store
        self.models=models or ModelGateway(settings)
        self.faq=FAQRepository(self.store,settings)
        self.retriever=Retriever(self.store,self.models,settings)
        self.kb=KBManager(self.store,self.models,settings)
        self.evidence=EvidenceEngine(self.evidence_store,EvidenceRetriever(self.retriever,self.evidence_store,self.faq),
            self.models.evidence_llm,top_k=settings.retrieval_k)
        self.locks=[threading.Lock() for _ in range(64)]

    def session_lock(self,sid):
        return self.locks[int(hashlib.sha256(sid.encode()).hexdigest()[:8],16)%len(self.locks)]

    @staticmethod
    def compatible(result):
        mapping={'supported':'answered','partial':'partial','conflicted':'conflicted','missing':'insufficient',
            'needs_clarification':'clarify','retry_required':'error','service_error':'error'}
        result['status']=mapping.get(result['overall_status'],'insufficient')
        index=result['retrieval_status'].get('index_status')
        if result['overall_status']=='missing':
            result['status']={'not_imported':'empty','pending':'indexing','failed':'index_failed',
                'unindexed':'indexing','migration_required':'indexing'}.get(index,'insufficient')
        result['sources']=[{**e,'type':e['source'],'id':e['evidence_id'],'file_id':e['document_id'],
            'file_name':e['source_name'],'title':e['source_name'],
            **{k:e['locator'][k] for k in ('page','section','slide') if k in e['locator']}}
            for e in result['citations']]
        for i,e in enumerate(result['citations']):
            result['answer']=result['answer'].replace('[来源](evidence:'+e['evidence_id']+')',f'[{i+1}]')
        return result

    def answer(self,query,sid,scope=None,as_of=None):
        if not isinstance(query,str) or not query.strip() or len(query)>2000:raise ValueError('问题必须为 1–2000 字符')
        ctx=normalize_context(query,scope=scope,as_of=as_of)
        self.store.check_session(sid);query=query.strip()
        with self.session_lock(sid):
            try:
                revision=self.store.revision()
                result=self._answer(query,sid,ctx)
                result.update(session_id=sid,knowledge_revision=revision,answer_mode='evidence_governed',query_scope=ctx.scope,as_of=ctx.as_of)
                if result['status']=='error':result['saved']=False;return result
                self.store.save(sid,query,result,revision)
                return result
            except Exception as exc:
                log.warning('Answer failed (%s)',type(exc).__name__)
                changed=str(exc)=='KNOWLEDGE_CHANGED'
                result=empty_result('retry_required' if changed else 'service_error','knowledge_changed' if changed else 'registry_error',
                    '资料在回答过程中发生变化，请重试。' if changed else '依赖服务异常，本次无法完成证据判断；这不表示知识库没有答案。')
                return {**self.compatible(result),'session_id':sid,'saved':False}

    def _answer(self,query,sid,ctx):
        if re.fullmatch(r'(你好|您好|hi|hello|在吗)[!！?？。\s]*',query,re.I) or re.fullmatch(r'(你是谁|你叫什么)[?？。\s]*',query):
            result=empty_result('non_factual','not_started',self.s.welcome)
            return {**result,'status':'greeting','sources':[]}
        history=self.store.history(sid,limit=6)
        followup=bool(re.search(r'^(那|它|这个|该|上述|刚才|其|还有|如果|那么|然后)|怎么办[？?]?$|呢[？?]?$',query))
        contextual=[r.get('retrieval_query',r['question']) for r in history[-1:] if r['status'] in {'answered','faq','partial'}
            and r.get('knowledge_revision')==self.store.revision()] if followup else []
        if followup and not contextual and len(query)<12:
            return self.compatible(empty_result('needs_clarification','not_started','请补充问题的具体对象或完整问题。'))
        retrieval_query='\n'.join(contextual+[query])[-4000:]
        result=self.evidence.answer(query,ctx,retrieval_query=retrieval_query)
        return {**self.compatible(result),'retrieval_query':retrieval_query,'retrieval_mode':'dense_bm25_rrf','reranker':self.s.reranker_backend}

    def public_history(self,sid):
        rows=self.store.history(sid);revision=self.store.revision()
        # History is not an answer cache. Old answers can contain newly restricted text.
        # Fail closed on every revision change, including pre-migration responses.
        for row in rows:
            if row.get('knowledge_revision')!=revision:
                row.update(answer='资料或复核结果已更新，此历史回答已隐藏，请重新查询。',status='insufficient',overall_status='stale',
                    sources=[],citations=[],claims=[],conflicts=[],missing_information=[],validation_status='stale')
        return rows

    def close(self):self.kb.close()
