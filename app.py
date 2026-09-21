"""FastAPI transport; all answers share IntegratedQASystem.answer."""
from contextlib import asynccontextmanager
from pathlib import Path
import asyncio
import collections
import json
import os
import secrets
import time
from typing import Optional
from fastapi import FastAPI,Depends,File,Form,Header,HTTPException,UploadFile,WebSocket,Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel,ConfigDict,Field
from starlette.websockets import WebSocketDisconnect
from base.config import Settings,ROOT
from new_main import IntegratedQASystem
from rag_qa.core.evidence_admin import install_admin_routes,validate_policy

class Question(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    query: str=Field(min_length=1,max_length=2000)
    session_id: Optional[str]=Field(default=None,max_length=36)
    scope: dict[str,str]=Field(default_factory=dict)
    as_of: Optional[str]=None

class Login(BaseModel):
    username: str=Field(max_length=100)
    password: str=Field(max_length=256)


def create_app(settings=None,models=None):
    s=settings or Settings.load()
    @asynccontextmanager
    async def lifespan(app):
        app.state.qa=IntegratedQASystem(s,models)
        yield
        await asyncio.to_thread(app.state.qa.close)
    app=FastAPI(title=s.name,lifespan=lifespan)
    app.mount('/static',StaticFiles(directory=ROOT/'static'),name='static')
    tokens={}; failures=collections.defaultdict(collections.deque)
    def qa(): return app.state.qa

    async def admin(authorization: Optional[str]=Header(None)):
        token=(authorization or '').removeprefix('Bearer ')
        if not authorization or not authorization.startswith('Bearer ') or tokens.get(token,0)<time.time():
            tokens.pop(token,None); raise HTTPException(401,'未登录或登录已过期')
        return token

    async def admin_actor(token=Depends(admin)):
        return s.admin_user
    install_admin_routes(app,lambda:qa().evidence_store,admin_actor)

    @app.get('/')
    def index(): return FileResponse(ROOT/'static/index.html')

    @app.get('/health')
    def health(): return {'status':'alive'}

    @app.get('/api/config')
    def configuration():
        return {'instance_id':qa().store.instance_id(),'name':s.name,'description':s.description,'welcome':s.welcome,'suggestions':s.suggestions,
                'faq_enabled':s.faq_enabled,'allow_general_chat':False,'answer_mode':'evidence_governed'}

    @app.get('/api/status')
    def status():
        return {**qa().store.status(),'model_check':'not_performed_by_this_endpoint',
                'note':'索引状态不代表模型服务健康；需真实问答验证','reranker':s.reranker_backend}

    @app.post('/api/create_session')
    def session(): return {'session_id':qa().store.create_session()}

    @app.get('/api/history/{sid}')
    def history(sid:str):
        try: rows=qa().public_history(sid)
        except ValueError as e: raise HTTPException(404,str(e))
        return {'session_id':sid,'history':rows}

    @app.delete('/api/history/{sid}')
    def clear(sid:str):
        try:
            with qa().session_lock(sid): qa().store.clear(sid)
        except ValueError as e: raise HTTPException(404,str(e))
        return {'status':'cleared'}

    @app.post('/api/query')
    def query(body:Question):
        sid=body.session_id or qa().store.create_session()
        try: return qa().answer(body.query,sid,body.scope,body.as_of)
        except ValueError as e: raise HTTPException(400,str(e))

    @app.websocket('/api/stream')
    async def stream(ws:WebSocket):
        origin=ws.headers.get('origin')
        if origin and origin not in {f'http://{ws.headers.get("host")}',f'https://{ws.headers.get("host")}'}:
            await ws.close(code=1008); return
        await ws.accept()
        try:
            while True:
                raw=await ws.receive_text()
                if len(raw)>12000:
                    await ws.send_json({'type':'error','error':'请求过长'}); continue
                try:
                    body=Question.model_validate_json(raw)
                    if not body.query.strip(): raise ValueError('empty question')
                except Exception:
                    await ws.send_json({'type':'error','error':'无效请求：仅支持 query 和 session_id，问题为 1–2000 字符'}); continue
                sid=body.session_id or qa().store.create_session()
                await ws.send_json({'type':'start','session_id':sid,'validation_status':'pending'})
                await ws.send_json({'type':'status','message':'正在检索资料并核验证据…'})
                try: result=await asyncio.to_thread(qa().answer,body.query,sid,body.scope,body.as_of)
                except ValueError as e:
                    await ws.send_json({'type':'error','error':str(e)}); continue
                if result['status']=='error':
                    await ws.send_json({'type':'end',**result}); continue
                await ws.send_json({'type':'meta',**{k:v for k,v in result.items() if k!='answer'}})
                # Only validated answer text is streamed, so unsupported claims never flash on screen.
                for offset in range(0,len(result['answer']),24):
                    await ws.send_json({'type':'token','token':result['answer'][offset:offset+24]})
                    await asyncio.sleep(0.005)
                await ws.send_json({'type':'end',**result})
        except WebSocketDisconnect: pass

    @app.post('/api/kb/login')
    def login(body:Login,request:Request):
        if not s.admin_password: raise HTTPException(503,'管理员密码未配置；请设置 KB_ADMIN_PASSWORD 并重启')
        ip=request.client.host if request.client else 'local'
        now=time.time(); queue=failures[ip]
        while queue and queue[0]<now-60: queue.popleft()
        if len(queue)>=10: raise HTTPException(429,'登录尝试过多，请稍后再试')
        if not (secrets.compare_digest(body.username.encode(),s.admin_user.encode()) and
                secrets.compare_digest(body.password.encode(),s.admin_password.encode())):
            queue.append(now); raise HTTPException(401,'账号或密码错误')
        for token,expires in list(tokens.items()):
            if expires<now: del tokens[token]
        token=secrets.token_urlsafe(32); tokens[token]=now+7200
        return {'token':token,'username':s.admin_user,'expires_in_minutes':120}

    @app.post('/api/kb/logout')
    def logout(token=Depends(admin)):
        tokens.pop(token,None); return {'status':'logged_out'}

    @app.get('/api/kb/files',dependencies=[Depends(admin)])
    def files(): return {'files':qa().kb.list_files()}

    async def read_upload(file):
        data=await file.read(s.max_upload_mb*1024*1024+1)
        await file.close()
        if len(data)>s.max_upload_mb*1024*1024: raise HTTPException(413,'文件超过大小限制')
        return data

    @app.post('/api/kb/upload',status_code=202,dependencies=[Depends(admin)])
    async def upload(file:UploadFile=File(...),policy:str=Form('{}')):
        data=await read_upload(file)
        try: return await asyncio.to_thread(qa().kb.upload,file.filename,data,validate_policy(json.loads(policy)))
        except (ValueError,TypeError) as e: raise HTTPException(400,'无效文档或证据元数据')

    @app.put('/api/kb/files/{fid}',status_code=202,dependencies=[Depends(admin)])
    async def replace_document(fid:str,file:UploadFile=File(...)):
        data=await read_upload(file)
        try:return qa().kb.replace(fid,data)
        except ValueError as e:raise HTTPException(400,str(e))

    @app.delete('/api/kb/files/{fid}',dependencies=[Depends(admin)])
    def delete(fid:str):
        try: return qa().kb.delete(fid)
        except ValueError as e: raise HTTPException(404,str(e))

    @app.post('/api/kb/files/{fid}/rebuild',status_code=202,dependencies=[Depends(admin)])
    def rebuild(fid:str):
        try: return qa().kb.rebuild(fid)
        except ValueError as e: raise HTTPException(400,str(e))

    @app.post('/api/kb/faq/import',dependencies=[Depends(admin)])
    async def import_faq(file:UploadFile=File(...),policy:Optional[str]=Form(None)):
        data=await read_upload(file)
        try: return await asyncio.to_thread(qa().faq.import_bytes,data,file.filename,validate_policy(json.loads(policy)) if policy else None)
        except (ValueError,UnicodeError) as e: raise HTTPException(400,str(e))

    return app

app=create_app()
if __name__=='__main__':
    import uvicorn
    uvicorn.run(app,host=os.getenv('HOST','127.0.0.1'),port=int(os.getenv('PORT','8080')))
