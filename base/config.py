"""INI settings; credentials only come from environment, never the old project."""
from dataclasses import dataclass
from pathlib import Path
import configparser
import json
import os

ROOT = Path(__file__).resolve().parents[1]

@dataclass(frozen=True)
class Settings:
    name: str
    description: str
    welcome: str
    suggestions: list
    data_dir: Path
    llm_url: str
    llm_model: str
    llm_key: str
    embedding_url: str
    embedding_model: str
    embedding_key: str
    reranker_model: str
    reranker_device: str
    reranker_backend: str
    retrieval_k: int
    candidate_m: int
    similarity_threshold: float
    rerank_threshold: float
    faq_enabled: bool
    faq_threshold: float
    faq_margin: float
    insufficient_action: str
    admin_user: str
    admin_password: str
    chunk_size: int
    chunk_overlap: int
    max_upload_mb: int
    model_timeout: int

    @classmethod
    def load(cls, filename=None):
        c = configparser.ConfigParser(interpolation=None)
        c.read(filename or os.getenv('ASSISTANT_CONFIG', str(ROOT / 'config.ini')), encoding='utf-8')
        def get(section, key, default, env=None):
            return os.getenv(env or f'ASSISTANT_{key.upper()}', c.get(section, key, fallback=str(default)))
        def flag(section, key, default):
            value=get(section,key,str(default).lower()).lower()
            if value not in {'true','false'}: raise ValueError(f'{key} must be true or false')
            return value == 'true'
        if flag('assistant','allow_general_chat',False):
            raise ValueError('此版本只提供知识问答；allow_general_chat 必须为 false')
        s = cls(
            get('assistant','name','知识问答助手'),
            get('assistant','description','依据你导入的常见问答与知识文档回答问题。'),
            get('assistant','welcome','你好，请描述你想了解的问题。我会依据已导入资料回答。'),
            json.loads(get('assistant','suggestions','[]')),
            Path(get('storage','data_dir',str(ROOT/'data'),'ASSISTANT_DATA_DIR')).expanduser().resolve(),
            get('model','llm_url','http://127.0.0.1:11434/v1','LLM_BASE_URL'),
            get('model','llm_model','qcwind/qwen3-8b-instruct-Q4-K-M:latest','LLM_MODEL'),
            os.getenv('LLM_API_KEY',''),
            get('model','embedding_url','http://127.0.0.1:11434/v1','EMBEDDING_BASE_URL'),
            get('model','embedding_model','bge-m3:latest','EMBEDDING_MODEL'),
            os.getenv('EMBEDDING_API_KEY',''),
            get('model','reranker_model','BAAI/bge-reranker-large','RERANKER_MODEL'),
            get('model','reranker_device','cpu','RERANKER_DEVICE'),
            get('model','reranker_backend','cross_encoder','RERANKER_BACKEND'),
            int(get('retrieval','retrieval_k',15)), int(get('retrieval','candidate_m',3)),
            float(get('retrieval','similarity_threshold',0.25)), float(get('retrieval','rerank_threshold',0.5)),
            flag('faq','enabled',True), float(get('faq','threshold',0.90)),float(get('faq','margin',0.15)),
            get('assistant','insufficient_action','clarify'),
            os.getenv('KB_ADMIN_USERNAME','admin'), os.getenv('KB_ADMIN_PASSWORD',''),
            int(get('retrieval','chunk_size',700)),int(get('retrieval','chunk_overlap',80)),
            int(get('storage','max_upload_mb',20)),int(get('model','model_timeout',180)),
        )
        if s.reranker_backend not in {'cross_encoder','disabled'}: raise ValueError('invalid reranker_backend')
        if not 0 <= s.chunk_overlap < s.chunk_size or s.chunk_size > 4000: raise ValueError('invalid chunk size/overlap')
        if not 1 <= s.candidate_m <= s.retrieval_k <= 100: raise ValueError('invalid retrieval counts')
        if s.insufficient_action not in {'clarify','refuse'}: raise ValueError('invalid insufficient_action')
        if not all(0 <= x <= 1 for x in [s.similarity_threshold,s.rerank_threshold,s.faq_threshold,s.faq_margin]): raise ValueError('thresholds must be in [0,1]')
        if s.max_upload_mb < 1 or s.model_timeout < 1: raise ValueError('invalid resource limit')
        if not isinstance(s.suggestions,list) or not all(isinstance(x,str) for x in s.suggestions): raise ValueError('suggestions must be a JSON list of strings')
        return s
