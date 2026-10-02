"""Extract exact repository PDFs and translate locally, with source-unit hashes.
Chinese output is unreviewed machine translation, never a publisher translation.
Roman page labels and code are preserved rather than passed to a language model.
"""
from pathlib import Path
import argparse, collections, gzip, hashlib, json, os, re, time, unicodedata
import urllib.request, zipfile
import fitz
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'reader'/'intermediate'
BOOKS=[('embedded','Making Embedded Systems','embedded/Making-Embedded-Systems-2nd-Edition.pdf'),('delivery','Continuous Delivery','devops/Continuous-Delivery.pdf'),('sre','Site Reliability Engineering','sre/Site-Reliability-Engineering.pdf')]
MODEL_URL='https://huggingface.co/TiberiuCristianLeon/Argostranslate/resolve/9dc7a2c8f29ac18e020c93f8208476d1e3fe9a9f/translate-en_zh-1_9.argosmodel'
MODEL_SHA256='433e7c4f034d87fbe2353161e05f18646d7999452f801a4e1f0378522b9850ab'
def key(text):return hashlib.sha256(text.encode('utf-8')).hexdigest()
def clean(text):return unicodedata.normalize('NFKC',text).replace('\u00ad','')
def extract():
    fitz.TOOLS.mupdf_display_errors(False);corpus=[]
    for slug,title,relative in BOOKS:
        path=ROOT/relative;doc=fitz.open(path)
        book=dict(slug=slug,title=title,source=relative,sha256=hashlib.sha256(path.read_bytes()).hexdigest(),page_count=len(doc),toc=doc.get_toc(),pages=[])
        for pn,page in enumerate(doc):
            blocks=[]
            data=page.get_text('dict',flags=(fitz.TEXTFLAGS_DICT|fitz.TEXT_DEHYPHENATE)&~fitz.TEXT_PRESERVE_IMAGES,sort=True)
            for bi,b in enumerate(data['blocks']):
                if b['type']!=0:continue
                lines=b.get('lines',[]);spans=[s for line in lines for s in line['spans']]
                texts=[clean(''.join(s['text']for s in line['spans']))for line in lines]
                if not ''.join(texts).strip():continue
                fonts=collections.Counter();mono=0
                for s in spans:
                    fonts[round(s['size'],1)]+=len(s['text'])
                    if any(x in s['font'].lower()for x in ('mono','courier','consolas','code')):mono+=len(s['text'])
                is_code=mono>.65*sum(fonts.values());text=('\n'if is_code else' ').join(texts).strip()
                footer=b['bbox'][1]>page.rect.height-45 or b['bbox'][3]<25
                blocks.append(dict(id=f'p{pn+1:04d}-b{bi:03d}',text=text,key=key(text),code=is_code,footer=footer,size=max(fonts),font=spans[0]['font'],bbox=list(b['bbox']),lines=texts))
            book['pages'].append(dict(number=pn+1,label=page.get_label(),width=page.rect.width,height=page.rect.height,blocks=blocks,raw=page.get_text()))
        corpus.append(book)
    return corpus

def all_units(corpus):
    units={}
    for book in corpus:
        for level,title,page in book['toc']:units[key(title)]=title
        for page in book['pages']:
            for b in page['blocks']:
                if not b['code']and re.search('[A-Za-z]',b['text']):units[b['key']]=b['text']
    return units

def download_model():
    home=ROOT/'reader'/'.model';home.mkdir(parents=True,exist_ok=True)
    metadata=list(home.rglob('metadata.json'))
    if not metadata:
        archive=home/'en-zh.argosmodel'
        urllib.request.urlretrieve(MODEL_URL,archive)
        assert hashlib.sha256(archive.read_bytes()).hexdigest()==MODEL_SHA256,'Model hash mismatch'
        with zipfile.ZipFile(archive)as z:
            for name in z.namelist():
                if home.resolve()not in (home/name).resolve().parents:raise ValueError('Unsafe model path')
            z.extractall(home)
        metadata=list(home.rglob('metadata.json'))
    if not metadata:raise RuntimeError('Model metadata missing')
    return metadata[0].parent

def translate(corpus,shard,count):
    import ctranslate2
    import sentencepiece as spm
    model=download_model();meta=json.loads((model/'metadata.json').read_text())
    tokenizer=spm.SentencePieceProcessor(model_file=str(model/'sentencepiece.model'))
    translator=ctranslate2.Translator(str(model/'model'),device='cpu',compute_type='int8',inter_threads=1,intra_threads=min(4,os.cpu_count()or 2))
    units=sorted(all_units(corpus).items())[shard::count]
    pieces=[];owners=[];outputs=collections.defaultdict(list);preserved=[]
    for uid,text in units:
        if re.fullmatch(r'[ivxlcdmIVXLCDM]+',text)and len(text)<=12:
            outputs[uid].append(text);preserved.append(uid);continue
        for sentence in re.split(r'(?<=[.!?])\s+(?=[A-Z\"\u201c\u2018(])',text):
            tokens=tokenizer.encode(sentence,out_type=str)
            for start in range(0,len(tokens),160):pieces.append(tokens[start:start+160]);owners.append(uid)
    prefix=meta.get('target_prefix')or None;started=time.monotonic()
    for start in range(0,len(pieces),64):
        batch=pieces[start:start+64]
        results=translator.translate_batch(batch,target_prefix=[[prefix]]*len(batch)if prefix else None,beam_size=2,length_penalty=.2,replace_unknowns=True,max_batch_size=2048,batch_type='tokens',max_input_length=0,max_decoding_length=768)
        for uid,result in zip(owners[start:start+64],results):
            value=tokenizer.decode(result.hypotheses[0]).strip()
            if prefix and value.startswith(prefix):value=value[len(prefix):].strip()
            if not value:raise RuntimeError(f'Empty translation: {uid}')
            outputs[uid].append(value)
        if start%640==0:print(f'shard={shard} segments={start}/{len(pieces)} seconds={time.monotonic()-started:.0f}',flush=True)
    result={uid:''.join(outputs[uid])for uid,_ in units}
    if len(result)!=len(units)or any(not v for v in result.values()):raise RuntimeError('Incomplete translation shard')
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/f'translations-{shard:02d}.json').write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')
    (OUT/f'provenance-{shard:02d}.json').write_text(json.dumps(dict(engine='CTranslate2',engine_version=ctranslate2.__version__,model=meta,model_url=MODEL_URL,model_sha256=hashlib.sha256((model/'model/model.bin').read_bytes()).hexdigest(),beam_size=2,reviewed=False,unit_count=len(result),structural_labels_preserved=preserved,segment_count=len(pieces),shard=shard,seconds=round(time.monotonic()-started,2)),ensure_ascii=False,indent=2),encoding='utf-8')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--shard',type=int,default=0);p.add_argument('--count',type=int,default=20);p.add_argument('--extract-only',action='store_true');args=p.parse_args();OUT.mkdir(parents=True,exist_ok=True)
    corpus=extract()
    if args.shard==0 or args.extract_only:
        with gzip.open(OUT/'corpus.json.gz','wt',encoding='utf-8')as f:json.dump(corpus,f,ensure_ascii=False)
    print('source pages',sum(b['page_count']for b in corpus),'unique translation units',len(all_units(corpus)),flush=True)
    if not args.extract_only:translate(corpus,args.shard,args.count)
