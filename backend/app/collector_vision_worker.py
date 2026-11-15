"""Bounded screen probe; never receives device/bot/database credentials."""
import csv,json,os,re,sys,tempfile
from io import BytesIO,StringIO
from PIL import Image,ImageOps
from .screenshot_ocr_worker import Engine


def main():
    if os.name=='posix':
        import resource
        resource.setrlimit(resource.RLIMIT_CORE,(0,0));resource.setrlimit(resource.RLIMIT_CPU,(20,20))
        resource.setrlimit(resource.RLIMIT_AS,(384*1024*1024,)*2)
    raw=sys.stdin.buffer.read(4*1024*1024+1)
    if len(raw)>4*1024*1024:raise ValueError()
    with Image.open(BytesIO(raw)) as original:
        if original.format not in {'PNG','JPEG'} or original.width*original.height>12_000_000:raise ValueError()
        original.verify()
    with Image.open(BytesIO(raw)) as original:image=ImageOps.exif_transpose(original).convert('RGB')
    with tempfile.TemporaryDirectory(prefix='so2-screen-') as scratch:
        engine=Engine(scratch)
        header=engine.read(image.crop((0,0,image.width,round(image.height*.24))),language='rus+eng',psm=6)
        match=re.search(r'(?:№|#|No\.?)[ \t]*([0-9]{5,20})',header,re.I) if re.search('матч|match',header,re.I) else None
        tsv=engine.read(image,language='rus+eng',psm=11,tsv=True)
        words=[]
        for row in csv.DictReader(StringIO(tsv),delimiter='\t'):
            text=row.get('text','').strip()
            if text and float(row.get('conf','-1'))>=70 and len(words)<250:
                words.append({'text':text[:64],**{key:int(row[key]) for key in ('left','top','width','height')}})
        joined=' '.join(w['text'] for w in words)
        uid=re.search(r'\b(?:ID|UID)\s*[:#]?\s*([0-9]{1,20})\b',joined,re.I)
        print(json.dumps({'match_id':match.group(1) if match else None,'detected_uid':uid.group(1) if uid else None,
                          'screen_type':'scoreboard_candidate' if match else 'profile_candidate' if uid else 'unknown',
                          'width':image.width,'height':image.height,'words':words},ensure_ascii=False))


if __name__=='__main__':
    try:main()
    except Exception:
        print(json.dumps({'error':'Screen could not be decoded safely'}));raise SystemExit(2)
