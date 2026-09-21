"""Browser-only failure fixture, no model. Run on an isolated port, never production."""
import asyncio
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fastapi import FastAPI,WebSocket
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import uvicorn
from base.config import ROOT
app=FastAPI()
app.mount('/static',StaticFiles(directory=ROOT/'static'),name='static')
@app.get('/')
def home():return FileResponse(ROOT/'static/index.html')
@app.get('/api/config')
def config():return {'instance_id':'stream-failure-fixture','name':'流式失败测试（模拟）','description':'仅前端回归','welcome':'','suggestions':[]}
@app.get('/api/status')
def status():return {'indexing_files':0,'failed_files':0,'indexed_files':0,'faq_count':0}
@app.post('/api/create_session')
def session():return {'session_id':'stream-failure-test'}
@app.get('/api/history/{sid}')
def history(sid:str):return {'session_id':sid,'history':[]}
@app.websocket('/api/stream')
async def stream(ws:WebSocket):
    await ws.accept();await ws.receive_json()
    await ws.send_json({'type':'start','validation_status':'pending'})
    await ws.send_json({'type':'token','token':'PROVISIONAL_UNSUPPORTED_999'})
    await asyncio.sleep(8)
    await ws.send_json({'type':'end','status':'answered','validation_status':'failed','answer':'UNSUPPORTED_FINAL_999','sources':[],
        'claims':[{'text':'UNSUPPORTED_FINAL_999','status':'supported'}],'conflicts':[]})
    await ws.close()
if __name__=='__main__':uvicorn.run(app,host='127.0.0.1',port=8085,log_level='warning')
