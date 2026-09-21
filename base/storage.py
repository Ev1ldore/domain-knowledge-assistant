"""Single-instance SQLite repositories. All mutation commits bump the knowledge revision."""
import contextlib
import json
import sqlite3
import threading
import time
import uuid

class Store:
    def __init__(self, root):
        root.mkdir(parents=True, exist_ok=True)
        self.path = root/'assistant.sqlite3'
        self.lock = threading.RLock()
        with self.connection() as db:
            db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT OR IGNORE INTO meta VALUES('revision','0');
            CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS history(id INTEGER PRIMARY KEY,session_id TEXT NOT NULL,
              question TEXT NOT NULL,response TEXT NOT NULL,created REAL NOT NULL,
              FOREIGN KEY(session_id) REFERENCES sessions(id));
            CREATE INDEX IF NOT EXISTS history_session ON history(session_id,id);
            CREATE TABLE IF NOT EXISTS faq(id TEXT PRIMARY KEY,question TEXT UNIQUE NOT NULL,
              answer TEXT NOT NULL,updated REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS files(id TEXT PRIMARY KEY,name TEXT UNIQUE NOT NULL,path TEXT NOT NULL,
              size INTEGER NOT NULL,status TEXT NOT NULL,error TEXT NOT NULL DEFAULT '',created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS chunks(id TEXT PRIMARY KEY,file_id TEXT NOT NULL,text TEXT NOT NULL,
              metadata TEXT NOT NULL,vector TEXT NOT NULL,
              FOREIGN KEY(file_id) REFERENCES files(id) ON DELETE CASCADE);
            ''')
            db.execute("INSERT OR IGNORE INTO meta VALUES('instance_id',?)",(str(uuid.uuid4()),))
            db.execute("UPDATE files SET status='failed',error='服务重启中断了索引，请重建' WHERE status IN ('uploaded','indexing')")

    @contextlib.contextmanager
    def connection(self):
        with self.lock:
            db=sqlite3.connect(self.path, timeout=15)
            db.row_factory=sqlite3.Row
            db.execute('PRAGMA foreign_keys=ON')
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback(); raise
            finally: db.close()

    @staticmethod
    def bump(db):
        db.execute("UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='revision'")

    def instance_id(self):
        with self.connection() as db:
            return db.execute("SELECT value FROM meta WHERE key='instance_id'").fetchone()[0]

    def revision(self):
        with self.connection() as db: return int(db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0])

    def create_session(self):
        sid=str(uuid.uuid4())
        with self.connection() as db: db.execute('INSERT INTO sessions VALUES(?,?)',(sid,time.time()))
        return sid

    def check_session(self,sid):
        with self.connection() as db:
            if not db.execute('SELECT 1 FROM sessions WHERE id=?',(sid,)).fetchone(): raise ValueError('会话不存在，请新建会话')

    def history(self,sid,limit=100):
        self.check_session(sid)
        with self.connection() as db:
            rows=db.execute('SELECT question,response,created FROM history WHERE session_id=? ORDER BY id DESC LIMIT ?', (sid,limit)).fetchall()
        return [{'question':r['question'],**json.loads(r['response']),'created':r['created']} for r in reversed(rows)]

    def save(self,sid,query,result,revision):
        with self.connection() as db:
            current=int(db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0])
            if current != revision: raise RuntimeError('KNOWLEDGE_CHANGED')
            db.execute('INSERT INTO history(session_id,question,response,created) VALUES(?,?,?,?)',
                       (sid,query,json.dumps(result,ensure_ascii=False),time.time()))

    def clear(self,sid):
        self.check_session(sid)
        with self.connection() as db: db.execute('DELETE FROM history WHERE session_id=?',(sid,))

    def status(self):
        with self.connection() as db:
            states={r[0]:r[1] for r in db.execute('SELECT status,count(*) FROM files GROUP BY status')}
            return {'files':sum(states.values()),'indexed_files':states.get('ready',0),
                    'indexing_files':states.get('uploaded',0)+states.get('indexing',0),
                    'failed_files':states.get('failed',0),'faq_count':db.execute('SELECT count(*) FROM faq').fetchone()[0],
                    'chunk_count':db.execute("SELECT count(*) FROM chunks JOIN files ON files.id=chunks.file_id WHERE files.status='ready'").fetchone()[0]}
