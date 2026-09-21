"""Real dense + BM25 retrieval, reciprocal rank fusion, optional cross-encoder."""
import json
import numpy as np
from rank_bm25 import BM25Plus
from mysql_qa.faq import tokens

class Retriever:
    def __init__(self,store,models,settings): self.store,self.models,self.s=store,models,settings

    def search(self,query,allowed_ids=None,max_results=None):
        with self.store.connection() as db:
            if allowed_ids is None:
                rows=[dict(r) for r in db.execute("SELECT c.*,f.name FROM chunks c JOIN files f ON f.id=c.file_id WHERE f.status='ready'")]
            else:
                rows=[dict(r) for r in db.execute("SELECT * FROM evidence_vectors WHERE id IN (SELECT value FROM json_each(?))",(json.dumps(allowed_ids),))]
            signature=db.execute("SELECT value FROM meta WHERE key='embedding'").fetchone()
        if not rows: return []
        query_vector=np.asarray(self.models.embeddings([query])[0],dtype=np.float32)
        if not signature or json.loads(signature[0]) != [self.models.fingerprint,len(query_vector)]:
            raise ValueError('嵌入模型或维度已变化；需要新的 data_dir 并重新导入资料')
        vectors=np.asarray([json.loads(r['vector']) for r in rows],dtype=np.float32)
        dense=vectors@query_vector
        sparse=BM25Plus([tokens(r['text']) for r in rows]).get_scores(tokens(query))
        top=min(self.s.retrieval_k,len(rows))
        dense_order=np.argsort(-dense)[:top]; sparse_order=np.argsort(-sparse)[:top]
        fusion={}
        for order in [dense_order,sparse_order]:
            for rank,i in enumerate(order): fusion[int(i)]=fusion.get(int(i),0)+1/(60+rank+1)
        candidates=sorted(fusion,key=fusion.get,reverse=True)[:top]
        candidates=[i for i in candidates if float(dense[i])>=self.s.similarity_threshold]
        if not candidates: return []
        scores=self.models.rerank(query,[rows[i]['text'] for i in candidates])
        if scores is None:
            scores=[float(dense[i]) for i in candidates]
            threshold=self.s.similarity_threshold
        else: threshold=self.s.rerank_threshold
        result=[]
        for i,score in zip(candidates,scores):
            if score<threshold: continue
            r=rows[i]
            result.append({'id':r['id'],'file_id':r['file_id'],'file_name':r['name'],'text':r['text'],
                'metadata':json.loads(r['metadata']),'score':round(score,4),'dense_score':round(float(dense[i]),4),
                'rrf_score':round(fusion[i],6),'type':'document'})
        return sorted(result,key=lambda r:r['score'],reverse=True)[:max_results if max_results is not None else self.s.candidate_m]


class EvidenceRetriever:
    def __init__(self,retriever,evidence,faq):
        self.retriever,self.evidence,self.faq=retriever,evidence,faq

    def hybrid_search_with_rerank(self,query,top_k,source_filter,allowed_parent_ids,max_results):
        from types import SimpleNamespace
        allowed=set(allowed_parent_ids)
        rows=self.retriever.search(query,allowed_parent_ids,max_results)
        result=[SimpleNamespace(page_content=r['text'],metadata={'parent_id':r['id']}) for r in rows]
        if self.faq.settings.faq_enabled:
            # FAQ retrieval is local BM25; ACL allowlist is applied before ranking.
            chunks=[c for c in self.evidence.records('chunk') if c['source']=='faq' and c['chunk_id'] in allowed]
            if chunks:
                scores=BM25Plus([tokens(c['file_name']+' '+c['quote']) for c in chunks]).get_scores(tokens(query))
                ranked=sorted(zip(chunks,scores),key=lambda x:float(x[1]),reverse=True)
                for c,score in ranked[:top_k]:
                    if set(tokens(query)) & set(tokens(c['file_name']+' '+c['quote'])):
                        result.append(SimpleNamespace(page_content=c['quote'],metadata={'parent_id':c['chunk_id']}))
        return result[:max_results]

    def evidence_index_state(self,source):
        docs=self.evidence.records('document')
        if any(d['status']=='failed' for d in docs):return 'failed'
        if any(d['status'] in {'pending','indexing'} for d in docs):return 'pending'
        return 'not_imported'
