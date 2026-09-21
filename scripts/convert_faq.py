"""Read a user-chosen CSV export, produce neutral FAQ JSON without touching any DB."""
import argparse
import csv
import json
from pathlib import Path

p=argparse.ArgumentParser()
p.add_argument('input',type=Path);p.add_argument('output',type=Path)
p.add_argument('--question-column',default='question');p.add_argument('--answer-column',default='answer')
a=p.parse_args()
if a.output.exists():p.error('输出文件已存在，拒绝覆盖；请选择新的输出路径')
rows=[]
with a.input.open(encoding='utf-8-sig',newline='') as f:
    for n,row in enumerate(csv.DictReader(f),2):
        question=row.get(a.question_column,'').strip();answer=row.get(a.answer_column,'').strip()
        if not question or not answer: p.error(f'第 {n} 行缺少问题或答案；未写入任何输出')
        rows.append({'question':question,'answer':answer})
a.output.write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
print(f'已转换 {len(rows)} 条；请检查内容后通过管理员入口导入。')
