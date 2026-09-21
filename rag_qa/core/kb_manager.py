"""Single serialized indexing worker, durable states and atomic index publication."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import logging
import shutil
import time
import uuid
from rag_qa.core.documents import SUPPORTED,chunks

log=logging.getLogger(__name__)

class KBManager:
    def __init__(self,store,models,settings):
        self.store,self.models,self.s=store,models,settings
        self.root=settings.data_dir/'uploads'; self.root.mkdir(exist_ok=True)
        self.worker=ThreadPoolExecutor(max_workers=1,thread_name_prefix='kb-index')

    def list_files(self):
        with self.store.connection() as db:
            return [dict(r) for r in db.execute('''SELECT f.id,f.name AS file_name,f.size,f.status,f.error,f.created,
                count(c.id) AS chunk_count FROM files f LEFT JOIN chunks c ON c.file_id=f.id GROUP BY f.id ORDER BY f.created DESC''')]

    def upload(self,name,data,policy=None):
        if not name or Path(name).name!=name or '\\' in name or any(ord(c)<32 for c in name) or len(name)>180: raise ValueError('无效文件名')
        if Path(name).suffix.lower() not in SUPPORTED: raise ValueError('仅支持 '+', '.join(sorted(SUPPORTED)))
        if not data or len(data)>self.s.max_upload_mb*1024*1024: raise ValueError('文件为空或超过大小限制')
        fid=str(uuid.uuid4()); folder=self.root/fid; path=folder/name
        with self.store.connection() as db:
            if db.execute('SELECT 1 FROM files WHERE name=?',(name,)).fetchone(): raise ValueError('同名文件已存在；请使用新文件名，或确认后删除旧文件')
            folder.mkdir(); path.write_bytes(data)
            try: db.execute('INSERT INTO files(id,name,path,size,status,created) VALUES(?,?,?,?,?,?)',(fid,name,str(path),len(data),'uploaded',time.time()))
            except Exception:
                path.unlink(); folder.rmdir(); raise
            if policy is not None:
                self.store.evidence._put(db,'document',fid,{'document_id':fid,'source':'document','file_name':name,
                    'status':'pending','initial_policy':policy,'permissions':policy['permissions']})
            self.store.evidence.sync(db); self.store.bump(db)
        self.worker.submit(self._index,fid)
        return {'id':fid,'file_name':name,'status':'uploaded','message':'上传完成，等待解析和索引；这不代表可以检索'}

    def rebuild(self,fid):
        with self.store.connection() as db:
            row=db.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone()
            if not row: raise ValueError('文件不存在')
            if row['status'] in {'uploaded','indexing'}: raise ValueError('文件正在索引')
            db.execute("UPDATE files SET status='uploaded',error='' WHERE id=?",(fid,)); self.store.evidence.sync(db); self.store.bump(db)
        self.worker.submit(self._index,fid)
        return {'id':fid,'status':'uploaded'}

    def _index(self,fid):
        try:
            with self.store.connection() as db:
                row=db.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone()
                if not row: return
                db.execute("UPDATE files SET status='indexing',error='' WHERE id=?",(fid,))
            fragments=chunks(row['path'],fid,self.s)
            vectors=self.models.embeddings([f['text'] for f in fragments])
            with self.store.connection() as db:
                if not db.execute('SELECT 1 FROM files WHERE id=?',(fid,)).fetchone(): return
                previous=db.execute("SELECT value FROM meta WHERE key='embedding'").fetchone()
                others=db.execute('SELECT count(*) FROM evidence_vectors').fetchone()[0]
                signature=json.dumps([self.models.fingerprint,len(vectors[0])])
                if previous and previous[0]!=signature and others: raise ValueError('嵌入配置改变；请使用新的 data_dir 重新导入全部资料')
                db.execute("INSERT OR REPLACE INTO meta VALUES('embedding',?)",(signature,))
                db.execute('DELETE FROM chunks WHERE file_id=?',(fid,))
                db.executemany('INSERT INTO chunks VALUES(?,?,?,?,?)',[(f['id'],fid,f['text'],json.dumps(f['metadata'],ensure_ascii=False),json.dumps(v)) for f,v in zip(fragments,vectors)])
                db.execute("UPDATE files SET status='ready',error='' WHERE id=?",(fid,)); self.store.evidence.sync(db); self.store.bump(db)
        except Exception as exc:
            # Never log provider response bodies, credentials or document contents.
            log.warning('Index failed (%s)',type(exc).__name__)
            message=str(exc) if isinstance(exc,(ValueError,UnicodeError)) else '解析或嵌入服务失败，请检查模型服务、文件格式后重建'
            with self.store.connection() as db:
                db.execute("UPDATE files SET status='failed',error=? WHERE id=?",(message[:200],fid)); self.store.evidence.sync(db); self.store.bump(db)

    def delete(self,fid):
        # DB deletion becomes visible atomically; in-flight jobs cannot resurrect a missing file ID.
        with self.store.connection() as db:
            row=db.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone()
            if not row: raise ValueError('文件不存在')
            db.execute('DELETE FROM files WHERE id=?',(fid,)); self.store.evidence.sync(db); self.store.bump(db)
        path=Path(row['path'])
        try:
            shutil.rmtree(self.root/fid)
        except OSError:
            log.warning('Index deleted; uploaded file cleanup failed for %s',fid)
            return {'status':'deleted','warning':'索引已删除，上传副本清理失败，请检查数据目录权限'}
        return {'status':'deleted'}

    def replace(self,fid,data):
        if not data or len(data)>self.s.max_upload_mb*1024*1024:raise ValueError('文件为空或超过大小限制')
        with self.store.connection() as db:
            row=db.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone()
            if not row:raise ValueError('文件不存在')
            if row['status'] in {'uploaded','indexing'}:raise ValueError('文件正在索引')
            # Keep the original document ID and archive the old upload and evidence.
            path=self.root/fid/(uuid.uuid4().hex+Path(row['name']).suffix)
            path.write_bytes(data)
            db.execute("UPDATE files SET path=?,size=?,status='uploaded',error='' WHERE id=?",(str(path),len(data),fid))
            self.store.evidence.sync(db);self.store.bump(db)
        self.worker.submit(self._index,fid)
        return {'id':fid,'status':'uploaded'}

    def close(self): self.worker.shutdown(wait=True,cancel_futures=True)
