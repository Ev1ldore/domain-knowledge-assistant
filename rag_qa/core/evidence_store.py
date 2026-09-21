"""Additive SQLite snapshots. Uses the application's transaction and revision."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def stable_id(*parts):
    return hashlib.sha256(json.dumps(parts,ensure_ascii=False,sort_keys=True).encode()).hexdigest()


def utcnow(): return datetime.now(timezone.utc).isoformat()


def default_policy():
    return {'permissions':{'visibility':'public','tenant':None},'scope':{},
        'source_name':None,'source_url':None,'origin_id':None,'published_at':None,'updated_at':None,
        'effective_from':None,'effective_to':None,'revoked_at':None,'superseded_at':None,
        'superseded_by':None,'replacement_basis':None,'authority':{'level':'unknown','basis':None},
        'source_kind':'document','speaker':None}


class EvidenceStore:
    def __init__(self,store,settings):
        self.store,self.s=store,settings
        with store.connection() as db:
            db.executescript((Path(__file__).resolve().parents[2]/'migrations/001_evidence.sql').read_text())
            changed=self.sync(db,backfill=True)
            if changed: store.bump(db)

    def revision(self): return self.store.revision()

    @staticmethod
    def _get(db,kind,key):
        row=db.execute('SELECT payload FROM evidence_records WHERE kind=? AND record_id=?',(kind,key)).fetchone()
        return json.loads(row[0]) if row else None

    def get(self,kind,key):
        with self.store.connection() as db: return self._get(db,kind,key)

    def records(self,kind):
        with self.store.connection() as db:
            return [json.loads(r[0]) for r in db.execute('SELECT payload FROM evidence_records WHERE kind=? ORDER BY record_id',(kind,))]

    def _put(self,db,kind,key,payload,actor='system',reason='index lifecycle',audit=True):
        old=self._get(db,kind,key)
        if old==payload:return False
        value=json.dumps(payload,ensure_ascii=False)
        db.execute('INSERT OR REPLACE INTO evidence_records VALUES(?,?,?)',(kind,key,value))
        if audit:
            db.execute('INSERT INTO evidence_audit(actor,reason,kind,record_id,before_value,after_value,created_at) VALUES(?,?,?,?,?,?,?)',
                (actor,reason,kind,key,json.dumps(old,ensure_ascii=False),value,utcnow()))
        return True

    def put_many(self,records,actor='system',reason='index lifecycle',audit=True):
        with self.store.connection() as db:
            for kind,key,payload in records:self._put(db,kind,key,payload,actor,reason,audit)
            self.store.bump(db)

    def save_observation(self,kind,key,payload):
        with self.store.connection() as db:self._put(db,kind,key,payload,audit=False)

    def audit(self,key):
        with self.store.connection() as db:
            return [dict(r) for r in db.execute('SELECT id,actor,reason,before_value AS "before",after_value AS "after",created_at FROM evidence_audit WHERE record_id=? ORDER BY id',(key,))]

    def sync(self,db,backfill=False):
        """Called within the SAME transaction as upload/index/delete/FAQ changes.
        Preserve old chunks, original segments, vectors, policies and audit rows.
        """
        from .documents import chunks
        changed=False; live=set()
        files=[dict(r) for r in db.execute('SELECT * FROM files')]
        for f in files:
            fid=f['id'];live.add(fid)
            doc=self._get(db,'document',fid) or {'document_id':fid,'source':'document','file_name':f['name']}
            state='pending' if f['status']=='uploaded' else f['status']
            changed=self._put(db,'document',fid,{**doc,'status':state}) or changed
            rows=[dict(r) for r in db.execute('SELECT * FROM chunks WHERE file_id=?',(fid,))]
            if f['status']!='ready' or not rows: continue
            # Old imports are enriched only when their current file reproduces the exact chunk IDs/text.
            if backfill and any('document_version' not in json.loads(r['metadata']) for r in rows):
                try: enriched={c['id']:c for c in chunks(f['path'],fid,self.s)}
                except (OSError,ValueError): enriched={}
                for r in rows:
                    c=enriched.get(r['id'])
                    if c and c['text']==r['text']:
                        r['metadata']=json.dumps(c['metadata'],ensure_ascii=False)
                        db.execute('UPDATE chunks SET metadata=? WHERE id=?',(r['metadata'],r['id']))
            grouped={}
            for r in rows:
                m=json.loads(r['metadata']);version=m.get('document_version') or 'legacy-unresolved'
                grouped.setdefault(version,[]).append((r,m))
            for version,fragments in grouped.items():
                vid=stable_id(fid,version);previous=self._get(db,'version',vid)
                policy=previous['policy'] if previous else {**doc.get('initial_policy',default_policy()),'permissions':doc.get('permissions',default_policy()['permissions'])}
                ids=[]
                for r,m in fragments:
                    cid=stable_id(fid,version,r['id']);ids.append(cid)
                    c={'chunk_id':cid,'document_id':fid,'document_version':version,'version_id':vid,
                        'quote':r['text'],'original':m.get('original_segment'),'locator':m.get('locator',{}),
                        'source':'document','file_name':f['name']}
                    changed=self._put(db,'chunk',cid,c) or changed
                    db.execute('INSERT OR IGNORE INTO evidence_vectors VALUES(?,?,?,?,?,?)',
                        (cid,fid,f['name'],r['text'],r['metadata'],r['vector']))
                v={'version_id':vid,'document_id':fid,'document_version':version,'source':'document',
                    'file_name':f['name'],'status':'ready','policy':policy,'parent_ids':sorted(ids)}
                changed=self._put(db,'version',vid,v) or changed
        # FAQ rows are evidence too. No direct-answer shortcut bypasses semantic validation.
        for r in db.execute('SELECT * FROM faq'):
            fid='faq:'+r['id'];live.add(fid);quote=r['answer'];version=stable_id(r['question'],quote);vid=stable_id(fid,version)
            old=self._get(db,'document',fid) or {}
            changed=self._put(db,'document',fid,{**old,'document_id':fid,'source':'faq','file_name':r['question'],'status':'ready'}) or changed
            previous=self._get(db,'version',vid);policy=previous['policy'] if previous else old.get('initial_policy',default_policy())
            policy={**policy,'permissions':old.get('permissions',policy['permissions'])}
            cid=stable_id(fid,version)
            changed=self._put(db,'version',vid,{'version_id':vid,'document_id':fid,'document_version':version,
                'source':'faq','file_name':r['question'],'status':'ready','policy':policy,'parent_ids':[cid]}) or changed
            changed=self._put(db,'chunk',cid,{'chunk_id':cid,'document_id':fid,'document_version':version,'version_id':vid,
                'quote':quote,'original':quote,'locator':{'kind':'faq_answer','start':0,'end':len(quote),'segment_hash':stable_id(quote)},
                'source':'faq','file_name':r['question']}) or changed
            # An explicit FAQ upsert is a replacement event, not an upload-time ranking rule.
            for vrow in list(db.execute("SELECT record_id,payload FROM evidence_records WHERE kind='version'")):
                v=json.loads(vrow['payload'])
                if v['document_id']==fid and v['version_id']!=vid and not v['policy'].get('superseded_at'):
                    day=datetime.fromtimestamp(r['updated'],timezone.utc).date().isoformat()
                    v['policy'].update(superseded_at=day,superseded_by=vid,replacement_basis='FAQ upsert explicitly replaced this answer')
                    changed=self._put(db,'version',v['version_id'],v) or changed
                    if not policy['effective_from']:
                        cur=self._get(db,'version',vid);cur['policy']['effective_from']=day
                        changed=self._put(db,'version',vid,cur) or changed
        for row in list(db.execute("SELECT record_id,payload FROM evidence_records WHERE kind='version'")):
            v=json.loads(row['payload']);doc=self._get(db,'document',v['document_id']) or {}
            if doc.get('permissions') and doc['permissions']!=v['policy']['permissions']:
                v['policy']['permissions']=doc['permissions'];changed=self._put(db,'version',v['version_id'],v) or changed
        for row in list(db.execute("SELECT record_id,payload FROM evidence_records WHERE kind='document'")):
            d=json.loads(row['payload'])
            if d['document_id'] not in live and d['status']!='deleted':
                changed=self._put(db,'document',d['document_id'],{**d,'status':'deleted'}) or changed
        return changed
