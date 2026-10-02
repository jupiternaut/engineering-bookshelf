"""Download a pinned Argos model mirror and smoke-test local CPU translation."""
from pathlib import Path
import hashlib, json, shutil, urllib.request, zipfile
import ctranslate2, sentencepiece
ROOT=Path(__file__).resolve().parent
URL='https://huggingface.co/TiberiuCristianLeon/Argostranslate/resolve/9dc7a2c8f29ac18e020c93f8208476d1e3fe9a9f/translate-en_zh-1_9.argosmodel'
SHA='433e7c4f034d87fbe2353161e05f18646d7999452f801a4e1f0378522b9850ab'
HOME=ROOT/'.model';HOME.mkdir(parents=True,exist_ok=True)
archive=HOME/'download.argosmodel'
request=urllib.request.Request(URL,headers={'User-Agent':'engineering-bookshelf/1.0'})
with urllib.request.urlopen(request,timeout=180) as response, archive.open('wb') as out:
    shutil.copyfileobj(response,out)
assert hashlib.sha256(archive.read_bytes()).hexdigest()==SHA, 'Model archive hash mismatch'
with zipfile.ZipFile(archive) as z:
    for item in z.infolist():
        destination=(HOME/item.filename).resolve()
        if HOME.resolve() not in destination.parents: raise ValueError('Unsafe archive member')
    z.extractall(HOME)
archive.unlink()
meta_path=next(HOME.rglob('metadata.json'));model=meta_path.parent
meta=json.loads(meta_path.read_text())
print('MODEL METADATA',json.dumps(meta,ensure_ascii=False),flush=True)
print('MODEL FILES',[str(p.relative_to(HOME)) for p in HOME.rglob('*') if p.is_file()],flush=True)
s=sentencepiece.SentencePieceProcessor(model_file=str(model/'sentencepiece.model'))
t=ctranslate2.Translator(str(model/'model'),device='cpu',compute_type='int8',intra_threads=4)
texts=['Software engineering is programming integrated over time.','An interrupt allows a peripheral device to request attention from the processor.','A deployment pipeline makes software releases repeatable and reliable.']
result=t.translate_batch([s.encode(x,out_type=str) for x in texts],beam_size=2,replace_unknowns=True,max_input_length=0)
translations=[s.decode(x.hypotheses[0]) for x in result]
assert all(translations)
print('TRANSLATION SMOKE TEST',json.dumps(list(zip(texts,translations)),ensure_ascii=False),flush=True)
(HOME/'download-provenance.json').write_text(json.dumps({'url':URL,'sha256':SHA,'smoke_test':list(zip(texts,translations))},ensure_ascii=False,indent=2),encoding='utf-8')
