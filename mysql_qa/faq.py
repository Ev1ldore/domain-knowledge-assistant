"""Neutral FAQ import and conservative BM25 matching; no shared Redis answer cache."""
import csv
import io
import json
import re
import time
import uuid
import unicodedata
from rank_bm25 import BM25Plus


def normalize(text):
    return re.sub(r'[\W_]+','',unicodedata.normalize('NFKC',text).lower())

def tokens(text):
    words=re.findall(r'[a-z0-9_]+|[\u4e00-\u9fff]',text.lower())
    # Include Chinese bigrams, avoid a domain-specific dictionary.
    return words+[a+b for a,b in zip(words,words[1:]) if len(a)==len(b)==1]

class FAQRepository:
    def __init__(self,store,settings): self.store,self.settings=store,settings

    def import_bytes(self,data,filename,policy=None):
        text=data.decode('utf-8-sig')
        if filename.lower().endswith('.csv'): rows=list(csv.DictReader(io.StringIO(text)))
        elif filename.lower().endswith('.json'): rows=json.loads(text)
        else: raise ValueError('FAQ 只支持 JSON 或 CSV')
        if not isinstance(rows,list) or not 1 <= len(rows) <= 10000: raise ValueError('FAQ 必须是 1–10000 条 question/answer 记录')
        prepared={}
        for row in rows:
            if not isinstance(row,dict): raise ValueError('FAQ 条目必须是对象')
            q,a=row.get('question'),row.get('answer')
            if not isinstance(q,str) or not isinstance(a,str) or not q.strip() or not a.strip(): raise ValueError('每条 FAQ 都必须有非空 question 和 answer')
            if len(q)>2000 or len(a)>20000: raise ValueError('FAQ 条目过长')
            q=q.strip()
            if not normalize(q): raise ValueError('FAQ 问题不能只有标点')
            key=normalize(q)
            if key in prepared: raise ValueError('导入文件含重复或归一化冲突的问题')
            prepared[key]=(q,a.strip())
        with self.store.connection() as db:
            existing={normalize(r['question']):r for r in db.execute('SELECT * FROM faq')}
            for key,(q,a) in prepared.items():
                if key in existing and existing[key]['question'] != q: raise ValueError('问题与已有 FAQ 归一化冲突')
                db.execute('INSERT INTO faq VALUES(?,?,?,?) ON CONFLICT(question) DO UPDATE SET answer=excluded.answer,updated=excluded.updated',
                           (str(uuid.uuid4()),q,a,time.time()))
            if policy is not None:
                for q,a in prepared.values():
                    row=db.execute('SELECT id FROM faq WHERE question=?',(q,)).fetchone();fid='faq:'+row['id']
                    old=self.store.evidence._get(db,'document',fid) or {}
                    self.store.evidence._put(db,'document',fid,{**old,'document_id':fid,'source':'faq','file_name':q,
                        'status':'ready','initial_policy':policy,'permissions':policy['permissions']})
            self.store.evidence.sync(db); self.store.bump(db)
        return {'imported':len(prepared),'mode':'upsert_by_question'}

    def search(self,query):
        if not self.settings.faq_enabled: return None
        with self.store.connection() as db: rows=[dict(r) for r in db.execute('SELECT * FROM faq ORDER BY id')]
        if not rows: return None
        n=normalize(query)
        for row in rows:
            if n==normalize(row['question']): return row
        qt=set(tokens(query))
        if not qt: return None
        scores=BM25Plus([tokens(r['question']) for r in rows]).get_scores(list(qt))
        ranked=sorted(range(len(rows)),key=lambda i:float(scores[i]),reverse=True)
        similarities=[]
        for i in ranked:
            rt=set(tokens(rows[i]['question']))
            similarities.append((len(qt & rt)/max(len(qt|rt),1),i))
        best,i=similarities[0]
        runner=max((v for v,j in similarities[1:]),default=0)
        if best >= self.settings.faq_threshold and best-runner >= self.settings.faq_margin: return rows[i]
        return None
